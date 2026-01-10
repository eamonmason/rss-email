"""Lambda function to retrieve batch results and send email via SES."""

import json
import logging
import os
from typing import Any, Dict, List

import anthropic
import boto3
from botocore.exceptions import ClientError

from .email_articles import send_via_ses, create_html, set_last_run
from .article_processor import ProcessedArticle

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


def retrieve_original_articles(bucket: str, metadata_key: str) -> List[Dict[str, Any]]:
    """
    Retrieve original article metadata from S3.

    Args:
        bucket: S3 bucket name
        metadata_key: S3 key for the metadata file

    Returns:
        List of original article dictionaries with complete metadata
    """
    s3_client = boto3.client("s3")
    try:
        response = s3_client.get_object(Bucket=bucket, Key=metadata_key)
        metadata = json.loads(response["Body"].read().decode("utf-8"))
        logger.info("Retrieved %d articles from S3 metadata", len(metadata.get("articles", [])))
        return metadata.get("articles", [])
    except ClientError as e:
        logger.error("Failed to retrieve article metadata from S3: %s", e)
        return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse article metadata from S3: %s", e)
        return []


def enrich_batch_results_with_metadata(
    categorized_data: Dict[str, List[Dict[str, Any]]],
    original_articles: List[Dict[str, Any]]
) -> Dict[str, List[ProcessedArticle]]:
    """
    Enrich batch results with metadata from original articles.

    Maps article IDs back to original data to restore comments and other fields
    that were not sent to Claude.

    Args:
        categorized_data: Categories dictionary from Claude API response
        original_articles: Original articles with complete metadata

    Returns:
        Dictionary of category names to lists of ProcessedArticle objects
    """
    enriched_categories = {}

    for category, articles in categorized_data.items():
        enriched_articles = []
        for article in articles:
            # Extract index from article ID (e.g., "article_5" -> 5)
            article_id = article.get("id", "")
            try:
                idx = int(article_id.split("_")[1])
                if idx < len(original_articles):
                    comments = original_articles[idx].get("comments", None)
                    original_desc = original_articles[idx].get("description", "")
                else:
                    logger.warning("Article index %d out of range", idx)
                    comments = None
                    original_desc = ""
            except (IndexError, ValueError):
                logger.warning("Invalid article ID: %s", article_id)
                comments = None
                original_desc = ""

            enriched_articles.append(ProcessedArticle(
                title=article.get("title", ""),
                link=article.get("link", ""),
                summary=article.get("summary", ""),
                category=category,
                pubdate=article.get("pubdate", ""),
                related_articles=article.get("related_articles", []),
                original_description=original_desc,
                comments=comments,
            ))

        enriched_categories[category] = enriched_articles

    return enriched_categories


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
        metadata_key = event.get("metadata_key")

        if batch_id is None:
            logger.info("No batch to retrieve (no articles to process)")
            return {"status": "success", "categories_count": 0, "failed_requests": 0}

        # Get configuration from environment
        api_key_param = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
        last_run_param = os.environ["LAST_RUN_PARAMETER"]
        source_email = os.environ["SOURCE_EMAIL_ADDRESS"]
        to_email = os.environ["TO_EMAIL_ADDRESS"]
        bucket = os.environ["RSS_BUCKET"]

        # Retrieve original articles from S3 if metadata_key is provided
        original_articles = []
        if metadata_key:
            original_articles = retrieve_original_articles(bucket, metadata_key)
            logger.info("Retrieved %d original articles from metadata", len(original_articles))
        else:
            logger.warning("No metadata_key provided, comments will not be available")

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

                    # Enrich and merge categories
                    if "categories" in categorized_data:
                        if original_articles:
                            # Enrich with metadata (adds comments and other fields)
                            enriched_categories = enrich_batch_results_with_metadata(
                                categorized_data["categories"],
                                original_articles
                            )
                            merge_categories(all_categories, enriched_categories)
                        else:
                            # Fallback: merge without enrichment
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
