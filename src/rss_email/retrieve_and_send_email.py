"""Lambda function to retrieve batch results and send email via SES."""

import json
import logging
import os
from typing import Any, Dict, List

import anthropic
import boto3

from .email_articles import send_via_ses, create_html, set_last_run

logger = logging.getLogger(__name__)


def merge_categories(
    target: Dict[str, List[Dict]], source: Dict[str, List[Dict]]
) -> None:
    """
    Merge article categories from source into target.

    Args:
        target: Target categories dictionary to merge into
        source: Source categories dictionary to merge from
    """
    for category, articles in source.items():
        if category not in target:
            target[category] = []
        target[category].extend(articles)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """
    Retrieve batch results, format email, and send via SES.

    Input (from event):
        {
            "batch_id": str,
            "request_counts": {...}
        }
    """
    try:
        batch_id = event["batch_id"]

        if batch_id is None:
            logger.info("No batch to retrieve (no articles to process)")
            return {"status": "success", "categories_count": 0, "failed_requests": 0}

        # Get configuration from environment
        api_key_param = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
        last_run_param = os.environ["LAST_RUN_PARAMETER"]
        source_email = os.environ["SOURCE_EMAIL_ADDRESS"]
        to_email = os.environ["TO_EMAIL_ADDRESS"]

        # Get API key from Parameter Store
        ssm = boto3.client("ssm")
        api_key = ssm.get_parameter(Name=api_key_param, WithDecryption=True)[
            "Parameter"
        ]["Value"]

        client = anthropic.Anthropic(api_key=api_key)

        # Stream results
        all_categories: Dict[str, List[Dict]] = {}
        failed_requests = []

        for result in client.messages.batches.results(batch_id):
            if result.result.type == "succeeded":
                try:
                    # Extract JSON from response
                    response_text = result.result.message.content[0].text

                    # Try direct JSON parsing first
                    try:
                        categorized_data = json.loads(response_text)
                    except json.JSONDecodeError:
                        # Try to extract JSON from markdown code blocks
                        if "```json" in response_text:
                            json_start = response_text.find("```json") + 7
                            json_end = response_text.find("```", json_start)
                            json_str = response_text[json_start:json_end].strip()
                            categorized_data = json.loads(json_str)
                        elif "```" in response_text:
                            json_start = response_text.find("```") + 3
                            json_end = response_text.find("```", json_start)
                            json_str = response_text[json_start:json_end].strip()
                            categorized_data = json.loads(json_str)
                        else:
                            # Try to find JSON object in response
                            json_start = response_text.find("{")
                            json_end = response_text.rfind("}") + 1
                            if 0 <= json_start < json_end:
                                json_str = response_text[json_start:json_end]
                                categorized_data = json.loads(json_str)
                            else:
                                exc = ValueError("No JSON found in response")
                                raise exc from None

                    # Merge categories
                    if "categories" in categorized_data:
                        merge_categories(all_categories, categorized_data["categories"])
                    else:
                        logger.warning(
                            "No categories in response for %s", result.custom_id
                        )

                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.error(
                        "Failed to parse response for %s: %s", result.custom_id, e
                    )
                    failed_requests.append(result)
            else:
                # Track failed requests for fallback
                logger.warning(
                    "Request %s failed with type %s",
                    result.custom_id,
                    result.result.type,
                )
                failed_requests.append(result)

        # Log results
        logger.info(
            "Processed batch: %d categories, %d failed requests",
            len(all_categories),
            len(failed_requests),
        )

        # Format and send email (reuse existing email formatting logic)
        html_content = create_html(all_categories)
        send_via_ses(to_email, source_email, "Your Daily RSS Digest", html_content)

        # Update last_run parameter
        set_last_run(last_run_param)

        logger.info("Successfully sent email with %d categories", len(all_categories))

        return {
            "status": "success",
            "categories_count": len(all_categories),
            "failed_requests": len(failed_requests),
        }

    except Exception as e:
        logger.error("Error retrieving and sending email: %s", e, exc_info=True)
        raise
