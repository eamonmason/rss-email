"""Lambda function that turns the day's RSS Brief into a single-narrator podcast episode."""

import logging
import os
from datetime import datetime
from typing import Any, Dict

import anthropic
import boto3
from botocore.exceptions import ClientError

from .article_processor import get_anthropic_api_key
from .podcast_generator import get_cloudfront_domain, update_podcast_feed, upload_to_s3
from .podcast_script import generate_narration, load_narration_input
from .podcast_tts import synthesize_speech

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_DESCRIPTION_TITLES = 3


def _episode_exists(bucket: str, key: str) -> bool:
    try:
        boto3.client("s3").head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def _description(narration_input: Dict[str, Any]) -> str:
    titles = [item["title"] for item in narration_input.get("must_read", [])[:MAX_DESCRIPTION_TITLES]]
    if not titles:
        return "The day's technology news, read aloud."
    return "Today: " + "; ".join(titles) + "."


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """Write, voice and publish the podcast for today's brief.

    Skips (returning ``audio_generated: False``) when there is no brief input for
    today or the episode is already published; raises on any real failure so
    Step Functions can alert.
    """
    bucket = os.environ["BUCKET"]
    today = datetime.now().strftime("%Y-%m-%d")

    narration_input = load_narration_input(bucket)
    if not narration_input or narration_input.get("date") != today:
        logger.info("No brief for %s; skipping podcast", today)
        return {"status": "skipped", "audio_generated": False}

    s3_key = f"podcasts/episodes/rss-brief-{today}.mp3"
    if _episode_exists(bucket, s3_key):
        logger.info("Episode %s already published; skipping", s3_key)
        return {"status": "skipped", "audio_generated": False}

    client = anthropic.Anthropic(api_key=get_anthropic_api_key())
    script = generate_narration(narration_input, client)
    if script is None:
        raise RuntimeError("Failed to write podcast narration")

    audio_data = synthesize_speech(script)
    if audio_data is None:
        raise RuntimeError("Failed to synthesise podcast audio")

    if not upload_to_s3(bucket, s3_key, audio_data, "audio/mpeg"):
        raise RuntimeError("Failed to upload podcast to S3")

    cloudfront_domain = None
    domain_param = os.environ.get("PODCAST_CLOUDFRONT_DOMAIN_PARAMETER")
    if domain_param:
        cloudfront_domain = get_cloudfront_domain(domain_param)
    host = cloudfront_domain or f"{bucket}.s3.amazonaws.com"
    audio_url = f"https://{host}/{s3_key}"

    if not update_podcast_feed(
        bucket=bucket,
        audio_url=audio_url,
        title=f"RSS Brief — {today}",
        description=_description(narration_input),
        pub_date=datetime.now().isoformat(),
        audio_size=len(audio_data),
        cloudfront_domain=cloudfront_domain,
        distribution_id=os.environ.get("PODCAST_CLOUDFRONT_DISTRIBUTION_ID"),
    ):
        raise RuntimeError("Failed to update podcast RSS feed")

    logger.info("Published podcast episode %s (%d bytes)", audio_url, len(audio_data))
    return {"status": "success", "audio_generated": True, "audio_url": audio_url,
            "audio_size": len(audio_data)}
