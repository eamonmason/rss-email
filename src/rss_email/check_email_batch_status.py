"""Lambda function to check status of Anthropic Message Batch."""

import logging
import os
from typing import Any, Dict

import anthropic
import boto3

logger = logging.getLogger(__name__)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """
    Check status of Message Batch.

    Input (from event):
        {
            "batch_id": str,
            "request_count": int
        }

    Returns:
        {
            "batch_id": str,
            "processing_status": str ("in_progress" | "ended" | "canceling"),
            "request_counts": {
                "processing": int,
                "succeeded": int,
                "errored": int,
                "canceled": int,
                "expired": int
            }
        }
    """
    try:
        batch_id = event["batch_id"]

        if batch_id is None:
            logger.info("No batch to check (no articles to process)")
            return {
                "batch_id": None,
                "processing_status": "ended",
                "request_counts": {
                    "processing": 0,
                    "succeeded": 0,
                    "errored": 0,
                    "canceled": 0,
                    "expired": 0,
                },
            }

        # Get API key from Parameter Store
        api_key_param = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
        ssm = boto3.client("ssm")
        api_key = ssm.get_parameter(Name=api_key_param, WithDecryption=True)[
            "Parameter"
        ]["Value"]

        client = anthropic.Anthropic(api_key=api_key)
        message_batch = client.messages.batches.retrieve(batch_id)

        logger.info(
            "Batch %s status: %s, succeeded: %d, errored: %d",
            batch_id,
            message_batch.processing_status,
            message_batch.request_counts.succeeded,
            message_batch.request_counts.errored,
        )

        # Return status to Step Functions
        return {
            "batch_id": message_batch.id,
            "processing_status": message_batch.processing_status,
            "request_counts": {
                "processing": message_batch.request_counts.processing,
                "succeeded": message_batch.request_counts.succeeded,
                "errored": message_batch.request_counts.errored,
                "canceled": message_batch.request_counts.canceled,
                "expired": message_batch.request_counts.expired,
            },
        }

    except Exception as e:
        logger.error("Error checking batch status: %s", e, exc_info=True)
        raise
