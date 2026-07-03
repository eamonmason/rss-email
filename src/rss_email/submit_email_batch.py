"""Lambda function to submit email batch to Anthropic Message Batches API."""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, Dict, List

import anthropic
import boto3
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from .email_articles import get_feed_file, filter_items, get_last_run
from .article_processor import (
    ClaudeRateLimiter,
    build_groups_for_articles,
    create_group_summary_prompt,
    _build_group_payload,
)
from .models import DEFAULT_CLAUDE_MODEL

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _split_groups_into_batches(
    groups: List[List[int]], max_batch_size: int
) -> List[List[tuple]]:
    """Split groups into list-of-batches of (global_group_id, indices) tuples."""
    batches: List[List[tuple]] = []
    for start in range(0, len(groups), max_batch_size):
        chunk = groups[start:start + max_batch_size]
        batches.append([
            (f"group_{start + offset}", indices)
            for offset, indices in enumerate(chunk)
        ])
    return batches


def create_batch_requests(
    group_batches: List[List[tuple]],
    articles: List[Dict[str, Any]],
    model: str,
) -> List[Request]:
    """
    Create one Message Batch request per group-batch.

    Args:
        group_batches: Output of _split_groups_into_batches.
        articles: Original article list (for resolving group member content).
        model: Claude model ID.
    """
    requests = []
    for idx, batch in enumerate(group_batches):
        payloads = [_build_group_payload(gid, indices, articles) for gid, indices in batch]
        prompt = create_group_summary_prompt(payloads)
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
    return requests


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
        model = os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
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
                "submitted_at": datetime.now(UTC).isoformat(),
                "articles_count": 0,
            }

        logger.info("Found %d articles to process", len(filtered_items))

        # Stage 1 (sync): group articles before submitting the summarize batch
        os.environ.setdefault("ANTHROPIC_API_KEY", api_key)
        rate_limiter = ClaudeRateLimiter()
        groups = build_groups_for_articles(filtered_items, rate_limiter)
        logger.info(
            "Grouped %d articles into %d logical articles",
            len(filtered_items),
            len(groups),
        )

        # Store original articles + groups in S3 so the downstream Lambda can
        # rebuild ProcessedArticle.sources without another Claude call.
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        metadata_key = f"batch-metadata/batch-{timestamp}.json"

        metadata = {
            "articles": filtered_items,
            "groups": groups,
            "submitted_at": datetime.now(UTC).isoformat(),
        }

        s3_client = boto3.client("s3")
        s3_client.put_object(
            Bucket=bucket,
            Key=metadata_key,
            Body=json.dumps(metadata, default=str),
            ContentType="application/json"
        )
        logger.info("Stored article + group metadata to S3: %s", metadata_key)

        # Split groups (not articles) into per-request batches
        group_batches = _split_groups_into_batches(groups, max_batch_size=batch_size)
        logger.info("Split into %d batch request(s)", len(group_batches))

        # Create batch requests targeting groups
        client = anthropic.Anthropic(api_key=api_key)
        requests = create_batch_requests(group_batches, filtered_items, model)

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
            "metadata_key": metadata_key,
            "request_count": len(requests),
            "submitted_at": datetime.now(UTC).isoformat(),
            "articles_count": len(filtered_items),
            "poll_count": 0,
        }

    except Exception as e:
        logger.error("Error submitting batch: %s", e, exc_info=True)
        raise
