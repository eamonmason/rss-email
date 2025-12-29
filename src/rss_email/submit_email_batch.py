"""Lambda function to submit email batch to Anthropic Message Batches API."""

import logging
import os
from datetime import datetime
from typing import Any, Dict

import anthropic
import boto3
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from .email_articles import get_feed_file, filter_items, get_last_run
from .article_processor import split_articles_into_batches, create_categorization_prompt

logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """
    Submit Message Batch to Anthropic API for email generation.

    Returns:
        {
            "batch_id": str,
            "request_count": int,
            "submitted_at": str (ISO timestamp)
        }
    """
    try:
        # Get configuration from environment
        bucket = os.environ["RSS_BUCKET"]
        key = os.environ["RSS_KEY"]
        last_run_param = os.environ["LAST_RUN_PARAMETER"]
        api_key_param = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
        model = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        batch_size = int(os.environ.get("CLAUDE_BATCH_SIZE", "25"))

        # Get API key from Parameter Store
        ssm = boto3.client("ssm")
        api_key = ssm.get_parameter(Name=api_key_param, WithDecryption=True)[
            "Parameter"
        ]["Value"]

        # Retrieve and filter articles
        run_date = get_last_run(last_run_param)
        rss_file = get_feed_file(bucket, key)
        filtered_items = filter_items(rss_file, run_date)

        if not filtered_items:
            logger.info("No articles to process")
            return {
                "batch_id": None,
                "request_count": 0,
                "submitted_at": datetime.utcnow().isoformat(),
                "articles_count": 0,
            }

        logger.info("Found %d articles to process", len(filtered_items))

        # Split into batches
        article_batches = split_articles_into_batches(
            filtered_items, max_batch_size=batch_size
        )
        logger.info("Split into %d batches", len(article_batches))

        # Create batch requests
        client = anthropic.Anthropic(api_key=api_key)
        requests = []

        for idx, batch in enumerate(article_batches):
            prompt = create_categorization_prompt(batch)
            requests.append(
                Request(
                    custom_id=f"email-batch-{idx}",
                    params=MessageCreateParamsNonStreaming(
                        model=model,
                        max_tokens=8192,
                        messages=[{"role": "user", "content": prompt}],
                    ),
                )
            )

        # Submit batch
        message_batch = client.messages.batches.create(requests=requests)

        logger.info(
            "Submitted batch %s with %d requests for %d articles",
            message_batch.id,
            len(requests),
            len(filtered_items),
        )

        # Return batch info to Step Functions
        return {
            "batch_id": message_batch.id,
            "request_count": len(requests),
            "submitted_at": datetime.utcnow().isoformat(),
            "articles_count": len(filtered_items),
        }

    except Exception as e:
        logger.error("Error submitting batch: %s", e, exc_info=True)
        raise
