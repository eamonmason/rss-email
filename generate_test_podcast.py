#!/usr/bin/env python3
"""
Generate a test podcast locally to review audio quality before deployment.

This script generates a short podcast using AWS Polly and saves it as an MP3 file.
Requires AWS credentials and Polly permissions.
"""

import os
import sys
sys.path.append('src')

import boto3  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

from rss_email.podcast_generator import (  # noqa: E402
    parse_speaker_segments,
    chunk_text,
    enhance_text_with_ssml,
    MARCO_VOICE,
    JOANNA_VOICE,
    POLLY_NEURAL_CHAR_LIMIT,
    SSML_ENABLED
)

# Sample podcast script (pre-written to avoid needing Claude API)
# Long lines are intentional for the script content  # noqa: E501
SAMPLE_SCRIPT = """Marco: Welcome to Eamon's Daily Tech News! I'm Marco, and today we have some exciting \
stories from the tech world.

Joanna: And I'm Joanna. Thanks for joining us! We're covering some interesting developments in AI, cloud \
computing, and cybersecurity.

Marco: That's right! Let's start with the big story. OpenAI has announced a revolutionary new reasoning \
model that shows significant improvements in complex problem-solving tasks. This is absolutely game-changing!

Joanna: This is fascinating, Marco. The new model uses a breakthrough technique called chain-of-thought \
reasoning, which allows it to break down complex problems into smaller, more manageable steps.

Marco: Exactly! Early benchmarks show it performing exceptionally well on mathematical and coding \
challenges. Developers are already finding innovative ways to integrate it into their applications.

Joanna: Speaking of development, there's also important news from Amazon Web Services. They've introduced a \
new serverless database service that promises to reduce costs by up to 70 percent compared to traditional \
database solutions. That's impressive!

Marco: Absolutely! This is a game-changer for startups and enterprises alike. The pay-per-request pricing \
model means you only pay for what you use, which could significantly reduce infrastructure costs.

Joanna: And finally, in cybersecurity news, researchers have discovered a new vulnerability affecting certain \
IoT devices. The good news is that patches are already available from major manufacturers.

Marco: Always important to keep those devices updated! That wraps up today's tech news roundup.

Joanna: Thanks for listening to Eamon's Daily Tech News. Stay curious, and we'll see you next time!

Marco: Take care, everyone!"""


def synthesize_speech_local(script: str, output_file: str = "test_podcast.mp3") -> bool:
    """
    Generate audio from script using AWS Polly and save locally.

    Args:
        script: Podcast script with speaker labels
        output_file: Path to save the MP3 file

    Returns:
        True if successful, False otherwise
    """
    print("Initializing AWS Polly client...")
    polly = boto3.client("polly")

    # Parse script into speaker segments
    print(f"\nParsing script ({len(script)} characters)...")
    segments = parse_speaker_segments(script)

    if not segments:
        print("❌ Error: No speaker segments found in script")
        return False

    print(f"✓ Found {len(segments)} speaker segments\n")

    # Synthesize each segment
    audio_chunks = []
    total_chunks = 0

    for i, (speaker, text) in enumerate(segments, 1):
        # Choose voice based on speaker
        voice_id = MARCO_VOICE if speaker == "Marco" else JOANNA_VOICE

        print(f"Segment {i}/{len(segments)}: {speaker}")
        print(f"  Text length: {len(text)} characters")
        print(f"  Voice: {voice_id}")

        # Chunk text if needed
        text_chunks = chunk_text(text, max_chars=POLLY_NEURAL_CHAR_LIMIT)
        total_chunks += len(text_chunks)

        if len(text_chunks) > 1:
            print(f"  Chunked into {len(text_chunks)} parts")

        for j, chunk in enumerate(text_chunks, 1):
            try:
                print(f"  Synthesizing chunk {j}/{len(text_chunks)}... ", end="", flush=True)

                # Enhance with SSML for more dynamic speech
                enhanced_chunk = enhance_text_with_ssml(chunk, speaker)

                response = polly.synthesize_speech(
                    Text=enhanced_chunk,
                    TextType="ssml" if SSML_ENABLED else "text",
                    OutputFormat="mp3",
                    VoiceId=voice_id,
                    Engine="neural"
                )

                audio_chunks.append(response["AudioStream"].read())
                print("✓")

            except ClientError as e:
                print("❌")
                print(f"\n❌ Error synthesizing speech: {e}")
                return False

    # Concatenate all audio chunks
    print(f"\nCombining {total_chunks} audio chunks...")
    combined_audio = b''.join(audio_chunks)

    # Save to file
    print(f"Saving to {output_file}...")
    try:
        with open(output_file, 'wb') as f:
            f.write(combined_audio)

        file_size_mb = len(combined_audio) / (1024 * 1024)
        print(f"✓ Saved {file_size_mb:.2f} MB to {output_file}")
        return True

    except IOError as e:
        print(f"❌ Error saving file: {e}")
        return False


def check_aws_access():
    """Check if AWS credentials are configured and Polly is accessible."""
    print("Checking AWS credentials and Polly access...")

    try:
        polly = boto3.client("polly")

        # Try to list voices to verify access
        response = polly.describe_voices(Engine="neural", LanguageCode="en-US")

        # Check if our required voices are available
        available_voices = [v['Id'] for v in response.get('Voices', [])]

        if MARCO_VOICE not in available_voices:
            print(f"⚠️  Warning: Voice '{MARCO_VOICE}' not found")
        if JOANNA_VOICE not in available_voices:
            print(f"⚠️  Warning: Voice '{JOANNA_VOICE}' not found")

        print("✓ AWS Polly access confirmed")
        print(f"✓ Region: {polly.meta.region_name}")
        print(f"✓ Available neural voices: {len(available_voices)}")
        return True

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'AccessDeniedException':
            print("❌ Access denied to AWS Polly")
            print("   Make sure your AWS credentials have 'polly:SynthesizeSpeech' permission")
        else:
            print(f"❌ AWS Error: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        print("   Make sure AWS credentials are configured (aws configure)")
        return False


def main():
    """Main function to generate test podcast."""
    print("\n" + "="*70)
    print("  PODCAST GENERATOR - LOCAL AUDIO TEST (SSML ENHANCED)")
    print("="*70)
    print("\nThis script will generate a test podcast using AWS Polly.")
    print("Enhanced with SSML for more dynamic and engaging speech!")
    print("The audio file will be saved locally for you to review.\n")

    # Check AWS access
    if not check_aws_access():
        print("\n❌ Cannot proceed without AWS Polly access")
        print("\nSetup instructions:")
        print("  1. Configure AWS credentials: aws configure")
        print("  2. Ensure your IAM user/role has 'polly:SynthesizeSpeech' permission")
        print("  3. Run this script again")
        sys.exit(1)

    print("\n" + "-"*70)
    print("Sample Script Preview:")
    print("-"*70)
    print(SAMPLE_SCRIPT[:300] + "...")
    print(f"\nTotal script length: {len(SAMPLE_SCRIPT)} characters")

    # Generate audio
    print("\n" + "-"*70)
    print("Generating Audio:")
    print("-"*70)

    output_file = "test_podcast.mp3"
    success = synthesize_speech_local(SAMPLE_SCRIPT, output_file)

    if success:
        print("\n" + "="*70)
        print("✅ SUCCESS! Test podcast generated successfully!")
        print("="*70)
        print(f"\nAudio file saved to: {os.path.abspath(output_file)}")
        print("\nTo play the podcast:")
        print(f"  macOS:   open {output_file}")
        print(f"  Linux:   xdg-open {output_file}")
        print(f"  Windows: start {output_file}")
        print("\nVoices used:")
        print(f"  Marco: {MARCO_VOICE} (US English, conversational)")
        print(f"  Joanna:  {JOANNA_VOICE} (US English, analytical)")
        print("\nSSML Enhancements applied:")
        print("  ✓ Faster speaking rate (Marco: 20% faster, Joanna: 22% faster)")
        print("  ✓ Strategic pauses between sentences (250ms)")
        print("  ✓ Natural pauses after questions (200ms)")
        print("  ✓ Phrasing pauses after commas (150ms)")
        print("\nIf you're happy with the audio quality, you're ready to deploy!")
        print("\nNext steps:")
        print("  1. Review the audio quality")
        print("  2. If satisfied, run: cdk deploy")
        print("  3. Subscribe to your podcast feed after deployment")
        print("")
    else:
        print("\n❌ Failed to generate podcast")
        sys.exit(1)


if __name__ == "__main__":
    main()
