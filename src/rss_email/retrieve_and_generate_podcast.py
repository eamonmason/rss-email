"""Lambda function to retrieve podcast batch results and generate audio."""

import logging
import os
from datetime import datetime
from typing import Any, Dict

import anthropic
import boto3

from .podcast_generator import (
    synthesize_speech,
    upload_to_s3,
    update_podcast_feed,
)
from .email_articles import set_last_run

logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """
    Retrieve podcast batch results, generate audio, and update RSS feed.

    Input (from event):
        {
            "batch_id": str,
            "request_counts": {...}
        }
    """
    try:
        batch_id = event["batch_id"]

        if batch_id is None:
            logger.info("No podcast batch to retrieve (no articles to process)")
            return {"status": "success", "audio_generated": False}

        # Get configuration from environment
        api_key_param = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
        last_run_param = os.environ["PODCAST_LAST_RUN_PARAMETER"]
        bucket = os.environ["BUCKET"]
        distribution_id = os.environ.get("PODCAST_CLOUDFRONT_DISTRIBUTION_ID")

        # Get API key from Parameter Store
        ssm = boto3.client("ssm")
        api_key = ssm.get_parameter(Name=api_key_param, WithDecryption=True)[
            "Parameter"
        ]["Value"]

        client = anthropic.Anthropic(api_key=api_key)

        # Retrieve batch results
        script = None
        for result in client.messages.batches.results(batch_id):
            if result.result.type == "succeeded":
                script = result.result.message.content[0].text
                logger.info("Retrieved podcast script (%d characters)", len(script))
                break
            logger.error(
                "Podcast script generation failed with type %s",
                result.result.type,
            )

        if script is None:
            logger.error("Failed to retrieve podcast script from batch")
            return {"status": "failed", "audio_generated": False}

        # Generate audio from script
        audio_data = synthesize_speech(script)
        if audio_data is None:
            logger.error("Failed to synthesize podcast audio")
            return {"status": "failed", "audio_generated": False}

        # Upload to S3
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        s3_key = f"podcasts/episodes/rss-digest-{timestamp}.mp3"

        upload_result = upload_to_s3(bucket, s3_key, audio_data, "audio/mpeg")
        if not upload_result:
            logger.error("Failed to upload podcast to S3")
            return {"status": "failed", "audio_generated": True}

        # Generate CloudFront URL
        cloudfront_domain_param = os.environ.get("PODCAST_CLOUDFRONT_DOMAIN_PARAMETER")
        if cloudfront_domain_param:
            cloudfront_domain = ssm.get_parameter(Name=cloudfront_domain_param)[
                "Parameter"
            ]["Value"]
            audio_url = f"https://{cloudfront_domain}/{s3_key}"
        else:
            audio_url = f"https://{bucket}.s3.amazonaws.com/{s3_key}"

        # Update podcast RSS feed
        title = f"RSS Digest - {datetime.now().strftime('%B %d, %Y')}"
        description = "Your daily digest of technology news and updates"
        pub_date = datetime.now().isoformat()

        update_result = update_podcast_feed(
            bucket=bucket,
            audio_url=audio_url,
            title=title,
            description=description,
            pub_date=pub_date,
            audio_size=len(audio_data),
            distribution_id=distribution_id,
        )

        if not update_result:
            logger.error("Failed to update podcast RSS feed")
            return {"status": "failed", "audio_generated": True}

        # Update last_run parameter
        set_last_run(last_run_param)

        logger.info("Successfully generated and published podcast")

        return {
            "status": "success",
            "audio_generated": True,
            "audio_url": audio_url,
            "audio_size": len(audio_data),
        }

    except Exception as e:
        logger.error("Error retrieving and generating podcast: %s", e, exc_info=True)
        raise
