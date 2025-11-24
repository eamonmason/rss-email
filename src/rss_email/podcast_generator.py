#!/usr/bin/env python3
"""Lambda function to generate a podcast from RSS articles."""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from xml.dom import minidom
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring

import boto3
import pydantic
from botocore.exceptions import ClientError

try:
    import anthropic
except ImportError:
    anthropic = None

from .email_articles import get_feed_file, filter_items, get_last_run, set_last_run

# Constants
PODCAST_PROMPT = """
You are creating an audio podcast for a tech news show called "Eamon's Daily Tech News".
Given the following list of articles, create an engaging 5-10 minute podcast that:

STRUCTURE:
- Opening: Warm welcome with today's date and a brief teaser of top stories
- Main Segments: Cover the most significant stories, grouped by theme (AI/ML, Business, Cybersecurity, etc.)
- Transitions: Natural segues between topics
- Closing: Brief recap and sign-off

STYLE GUIDELINES:
- Two hosts: Marco (enthusiastic, detail-oriented) and John (analytical, asks clarifying questions)
- Conversational tone - like two knowledgeable friends discussing the news
- Explain technical concepts in accessible terms
- Add context: why each story matters, potential implications
- Include brief reactions, insights, or predictions where appropriate
- Keep explanations concise but informative
- Natural dialogue with occasional back-and-forth

EMPHASIS:
- Prioritize stories with significant impact or interesting implications
- Connect related stories when relevant
- Avoid reading headlines verbatim - synthesize the information naturally
- Skip minor or redundant updates unless they add unique value
- Stick to the facts, use information in the article text provided, or that is historically accurate and verified.
- Conclude with one or two lighter articles that are fun or nerdy

FORMAT REQUIREMENTS (CRITICAL):
- Mark each speaker change with "Marco:" or "John:" at the start of their dialogue
- Example format:
  Marco: Welcome to Eamon's Daily Tech News! I'm Marco.
  John: And I'm John. Today we're covering some exciting developments in AI.
  Marco: That's right! Let's dive in...

Articles to cover:
"""

# AWS Polly limits
POLLY_NEURAL_CHAR_LIMIT = 3000
MARCO_VOICE = "Matthew"  # US English male, conversational
JOHN_VOICE = "Joanna"    # US English female, animated and engaging

# SSML configuration for more dynamic speech
SSML_ENABLED = True
MARCO_SPEAKING_RATE = "120%"  # Fast and energetic
JOHN_SPEAKING_RATE = "122%"   # Slightly faster for animated delivery

# Chunking configuration
MIN_CONTENT_SIZE = 100  # Minimum content size after accounting for SSML wrapper overhead
SENTENCE_BOUNDARY_PATTERN = r'([.!?]+(?:<break[^>]*/>)?\s+)'  # Matches sentence endings with optional SSML break tags

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@pydantic.validate_call(validate_return=True)
def parse_speaker_segments(script: str) -> List[Tuple[str, str]]:
    """
    Parse script into segments with speaker identification.

    Args:
        script: Full podcast script with "Marco:" and "John:" speaker labels

    Returns:
        List of tuples (speaker, text) where speaker is 'Marco' or 'John'
    """
    segments = []
    lines = script.split('\n')
    current_speaker = "Marco"  # Default speaker
    current_text = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if line starts with speaker label
        marco_match = re.match(r'^Marco:\s*(.*)', line, re.IGNORECASE)
        john_match = re.match(r'^John:\s*(.*)', line, re.IGNORECASE)

        if marco_match:
            # Save previous segment if exists
            if current_text:
                segments.append((current_speaker, ' '.join(current_text)))
                current_text = []
            current_speaker = "Marco"
            if marco_match.group(1):
                current_text.append(marco_match.group(1))
        elif john_match:
            # Save previous segment if exists
            if current_text:
                segments.append((current_speaker, ' '.join(current_text)))
                current_text = []
            current_speaker = "John"
            if john_match.group(1):
                current_text.append(john_match.group(1))
        else:
            current_text.append(line)

    # Save final segment
    if current_text:
        segments.append((current_speaker, ' '.join(current_text)))

    return segments


@pydantic.validate_call(validate_return=True)
def chunk_text(text: str, max_chars: int = POLLY_NEURAL_CHAR_LIMIT) -> List[str]:
    """
    Split text into chunks at sentence boundaries to stay under char limit.

    Handles both plain text and SSML-enhanced text by recognizing sentence
    boundaries even when SSML break tags are present.

    Args:
        text: Text to chunk (plain or SSML-enhanced)
        max_chars: Maximum characters per chunk (default: 3000 for Polly neural)

    Returns:
        List of text chunks, each under max_chars
    """
    if len(text) <= max_chars:
        return [text]

    chunks = []
    sentences = re.split(SENTENCE_BOUNDARY_PATTERN, text)
    current_chunk = ""

    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        separator = sentences[i + 1] if i + 1 < len(sentences) else ""
        combined = sentence + separator

        if len(current_chunk) + len(combined) <= max_chars:
            current_chunk += combined
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = combined

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


@pydantic.validate_call(validate_return=True)
def enhance_text_with_ssml(text: str, speaker: str) -> str:
    """
    Enhance text with SSML tags for more dynamic speech.

    Uses only SSML features supported by AWS Polly Neural voices:
    - Speaking rate adjustment
    - Strategic pauses

    Args:
        text: Plain text to enhance
        speaker: Speaker name ("Marco" or "John") for voice-specific settings

    Returns:
        SSML-enhanced text
    """
    if not SSML_ENABLED:
        return text

    # Choose speaking rate based on speaker
    rate = MARCO_SPEAKING_RATE if speaker == "Marco" else JOHN_SPEAKING_RATE

    # Add minimal pauses for natural phrasing without slowing down
    enhanced = text
    enhanced = re.sub(r'([.!])\s+', r'\1<break time="250ms"/> ', enhanced)
    enhanced = re.sub(r'([?])\s+', r'\1<break time="200ms"/> ', enhanced)

    # Very brief pauses after commas
    enhanced = re.sub(r'([,])\s+', r'\1<break time="150ms"/> ', enhanced)

    # Wrap in prosody for overall speaking rate
    # Note: Neural voices don't support pitch or emphasis tags
    ssml = f'<speak><prosody rate="{rate}">{enhanced}</prosody></speak>'

    return ssml


@pydantic.validate_call(validate_return=True)
def chunk_ssml_text(ssml_text: str, speaker: str, max_chars: int = POLLY_NEURAL_CHAR_LIMIT) -> List[str]:
    """
    Split SSML-enhanced text into chunks at sentence boundaries while maintaining valid SSML.

    Each chunk will be re-wrapped with proper SSML tags to ensure validity.

    Args:
        ssml_text: SSML-enhanced text to chunk
        speaker: Speaker name for re-wrapping chunks
        max_chars: Maximum characters per chunk (default: 3000 for Polly neural)

    Returns:
        List of valid SSML chunks, each under max_chars
    """
    if len(ssml_text) <= max_chars:
        return [ssml_text]

    # Extract the content between <prosody> tags
    prosody_match = re.search(r'<speak><prosody[^>]*>(.*)</prosody></speak>', ssml_text, re.DOTALL)
    if not prosody_match:
        # If no prosody tags found, treat as plain text
        return chunk_text(ssml_text, max_chars)

    inner_content = prosody_match.group(1)
    rate = MARCO_SPEAKING_RATE if speaker == "Marco" else JOHN_SPEAKING_RATE

    # Calculate overhead for SSML wrapper tags
    wrapper_overhead = len(f'<speak><prosody rate="{rate}"></prosody></speak>')

    # Chunk the inner content with reduced max to account for wrapper
    adjusted_max = max_chars - wrapper_overhead
    if adjusted_max < MIN_CONTENT_SIZE:
        # If wrapper is too large, just chunk as-is
        adjusted_max = max_chars

    chunks = []
    sentences = re.split(SENTENCE_BOUNDARY_PATTERN, inner_content)
    current_chunk = ""

    for i in range(0, len(sentences), 2):
        sentence = sentences[i]
        separator = sentences[i + 1] if i + 1 < len(sentences) else ""
        combined = sentence + separator

        if len(current_chunk) + len(combined) <= adjusted_max:
            current_chunk += combined
        else:
            if current_chunk:
                # Wrap chunk with SSML tags
                wrapped = f'<speak><prosody rate="{rate}">{current_chunk.strip()}</prosody></speak>'
                chunks.append(wrapped)
            current_chunk = combined

    if current_chunk:
        wrapped = f'<speak><prosody rate="{rate}">{current_chunk.strip()}</prosody></speak>'
        chunks.append(wrapped)

    return chunks


@pydantic.validate_call(validate_return=True)
def generate_script(articles: List[Dict[str, Any]]) -> Optional[str]:
    """Generate a podcast script using Claude."""
    if not anthropic:
        logger.error("Anthropic library not installed.")
        return None

    api_key_param = os.environ.get("ANTHROPIC_API_KEY_PARAMETER")
    if not api_key_param:
        logger.error("ANTHROPIC_API_KEY_PARAMETER not set.")
        return None

    try:
        ssm = boto3.client("ssm")
        parameter = ssm.get_parameter(Name=api_key_param, WithDecryption=True)
        api_key = parameter["Parameter"]["Value"]
    except ClientError as e:
        logger.error("Failed to retrieve Anthropic API key: %s", e)
        return None

    client = anthropic.Anthropic(api_key=api_key)

    articles_text = ""
    for article in articles:
        articles_text += f"Title: {article.get('title')}\n"
        articles_text += f"Description: {article.get('description')}\n"
        articles_text += "---\n"

    try:
        message = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-3-5-haiku-latest"),
            max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "4000")),
            messages=[
                {"role": "user", "content": PODCAST_PROMPT + "\n" + articles_text}
            ]
        )
        return message.content[0].text
    except (anthropic.APIError, anthropic.APIConnectionError, RuntimeError, ValueError) as e:
        logger.error("Error generating script with Claude: %s", e)
        return None


@pydantic.validate_call(validate_return=True)
def synthesize_speech(script: str) -> Optional[bytes]:
    """
    Convert script to speech using AWS Polly with two-host voice switching.

    Parses script to identify Marco and John segments, chunks text to stay
    under Polly's 3000 character limit, and synthesizes each chunk with the
    appropriate voice.

    Args:
        script: Full podcast script with speaker labels

    Returns:
        Combined MP3 audio data, or None on error
    """
    polly = boto3.client("polly")

    # Parse script into speaker segments
    segments = parse_speaker_segments(script)
    if not segments:
        logger.warning("No speaker segments found in script")
        return None

    logger.info("Found %d speaker segments", len(segments))

    # Synthesize each segment
    audio_chunks = []
    for speaker, text in segments:
        # Choose voice based on speaker
        voice_id = MARCO_VOICE if speaker == "Marco" else JOHN_VOICE

        # Enhance with SSML first, then chunk to ensure chunks stay under limit
        enhanced_text = enhance_text_with_ssml(text, speaker)

        # Chunk the SSML-enhanced text if needed, maintaining valid SSML in each chunk
        text_chunks = chunk_ssml_text(enhanced_text, speaker)
        logger.info("Synthesizing %d chunks for %s", len(text_chunks), speaker)

        for chunk in text_chunks:
            try:
                response = polly.synthesize_speech(
                    Text=chunk,
                    TextType="ssml" if SSML_ENABLED else "text",
                    OutputFormat="mp3",
                    VoiceId=voice_id,
                    Engine="neural"
                )
                audio_chunks.append(response["AudioStream"].read())
            except ClientError as e:
                logger.error("Error synthesizing speech chunk: %s", e)
                return None

    # Concatenate all audio chunks
    if not audio_chunks:
        logger.error("No audio chunks generated")
        return None

    return b''.join(audio_chunks)


@pydantic.validate_call(validate_return=True)
def upload_to_s3(bucket: str, key: str, data: bytes, content_type: str) -> bool:
    """Upload data to S3."""
    s3 = boto3.client("s3")
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type
        )
        return True
    except ClientError as e:
        logger.error("Error uploading to S3: %s", e)
        return False


@pydantic.validate_call(validate_return=True)
def update_podcast_feed(
    bucket: str,
    audio_url: str,
    title: str,
    description: str,
    pub_date: str
) -> bool:
    """
    Update or create the podcast RSS feed with a new episode.

    Args:
        bucket: S3 bucket name
        audio_url: Public URL to the podcast audio file
        title: Episode title
        description: Episode description
        pub_date: Publication date in ISO format

    Returns:
        True if successful, False otherwise
    """
    s3 = boto3.client("s3")
    feed_key = "podcasts/feed.xml"

    # Try to read existing feed
    try:
        response = s3.get_object(Bucket=bucket, Key=feed_key)
        feed_content = response['Body'].read().decode('utf-8')
        # Parse existing feed to extract items
        root = ElementTree.fromstring(feed_content)
        channel = root.find('channel')
        existing_items = list(channel.findall('item')) if channel is not None else []
    except s3.exceptions.NoSuchKey:
        logger.info("No existing feed found, creating new one")
        existing_items = []
    except (ClientError, ElementTree.ParseError, UnicodeDecodeError) as e:
        logger.warning("Error reading existing feed, creating new one: %s", e)
        existing_items = []

    # Create new RSS feed
    rss = Element('rss', version='2.0')
    rss.set('xmlns:itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')
    rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')

    channel = SubElement(rss, 'channel')

    # Channel metadata
    SubElement(channel, 'title').text = "Eamon's Daily Tech News"
    SubElement(channel, 'link').text = f"https://{bucket}.s3.amazonaws.com/podcasts/feed.xml"
    SubElement(channel, 'description').text = (
        "Daily tech news podcast covering AI/ML, cybersecurity, and technology trends"
    )
    SubElement(channel, 'language').text = 'en-us'

    # iTunes-specific tags
    SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}author').text = (
        'Eamon Mason'
    )
    SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit').text = 'no'

    # Add new episode as first item
    item = SubElement(channel, 'item')
    SubElement(item, 'title').text = title
    SubElement(item, 'description').text = description
    SubElement(item, 'pubDate').text = (
        datetime.fromisoformat(pub_date).strftime('%a, %d %b %Y %H:%M:%S GMT')
    )
    SubElement(item, 'guid').text = audio_url

    # Enclosure (audio file)
    enclosure = SubElement(item, 'enclosure')
    enclosure.set('url', audio_url)
    enclosure.set('type', 'audio/mpeg')

    # Add existing items (up to 10 most recent)
    for existing_item in existing_items[:9]:
        channel.append(existing_item)

    # Generate XML
    xml_str = tostring(rss, encoding='unicode')
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent='  ')

    # Remove extra blank lines
    pretty_xml = '\n'.join([line for line in pretty_xml.split('\n') if line.strip()])

    # Upload to S3
    try:
        s3.put_object(
            Bucket=bucket,
            Key=feed_key,
            Body=pretty_xml.encode('utf-8'),
            ContentType='application/rss+xml'
        )
        logger.info("Podcast feed updated successfully at s3://%s/%s", bucket, feed_key)
        return True
    except ClientError as e:
        logger.error("Error uploading podcast feed: %s", e)
        return False


@pydantic.validate_call
def generate_podcast(_event: Dict[str, Any], _context: Optional[Any] = None) -> None:
    """
    Lambda handler for podcast generation.

    Args:
        _event: Lambda event data (unused)
        _context: Lambda context (unused)
    """
    logger.info("Starting podcast generation")

    bucket = os.environ["BUCKET"]
    rss_key = os.environ["KEY"]
    podcast_prefix = "podcasts/episodes/"
    last_run_param = os.environ.get(
        "PODCAST_LAST_RUN_PARAMETER",
        "rss-podcast-lastrun"
    )

    # 1. Get Articles
    run_date = get_last_run(last_run_param)
    rss_file = get_feed_file(bucket, rss_key)
    filtered_items = filter_items(rss_file, run_date)

    if not filtered_items:
        logger.info("No new articles to process.")
        return

    logger.info("Found %d new articles.", len(filtered_items))

    # 2. Generate Script
    script = generate_script(filtered_items)
    if not script:
        logger.error("Failed to generate script.")
        return

    logger.info("Script generated successfully (length: %d chars)", len(script))

    # 3. Synthesize Audio with voice switching and chunking
    audio_data = synthesize_speech(script)
    if not audio_data:
        logger.error("Failed to synthesize audio.")
        return

    logger.info("Audio synthesized successfully (%d bytes)", len(audio_data))

    # 4. Upload Audio
    filename = f"podcast_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
    s3_key = f"{podcast_prefix}{filename}"
    if not upload_to_s3(bucket, s3_key, audio_data, "audio/mpeg"):
        logger.error("Failed to upload podcast.")
        return

    logger.info("Podcast uploaded to s3://%s/%s", bucket, s3_key)

    # 5. Update Feed
    audio_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"
    episode_title = f"Daily Tech News - {datetime.now().strftime('%Y-%m-%d')}"
    episode_description = (
        f"Tech news roundup for {datetime.now().strftime('%B %d, %Y')} "
        f"covering {len(filtered_items)} stories"
    )

    if not update_podcast_feed(
        bucket,
        audio_url,
        episode_title,
        episode_description,
        datetime.now().isoformat()
    ):
        logger.error("Failed to update podcast feed.")
        return

    logger.info("Podcast feed updated successfully")

    # 6. Update Last Run
    set_last_run(last_run_param)
    logger.info("Podcast generation completed successfully")


if __name__ == "__main__":
    # Local testing
    pass
