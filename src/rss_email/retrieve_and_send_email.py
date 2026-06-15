"""Lambda function to retrieve batch results and send email via SES."""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import anthropic
import boto3
from botocore.exceptions import ClientError

from .email_articles import send_via_ses, create_html, set_last_run
from .article_processor import (
    ProcessedArticle,
    _article_to_source,
    _iter_category_entries,
)
from .brief_generator import generate_brief
from .models import ArticleSource

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


def retrieve_batch_metadata(
    bucket: str, metadata_key: str
) -> Tuple[List[Dict[str, Any]], List[List[int]]]:
    """Retrieve original articles + group assignments from S3."""
    s3_client = boto3.client("s3")
    try:
        response = s3_client.get_object(Bucket=bucket, Key=metadata_key)
        metadata = json.loads(response["Body"].read().decode("utf-8"))
        articles = metadata.get("articles", [])
        groups = metadata.get("groups") or [[i] for i in range(len(articles))]
        logger.info(
            "Retrieved %d articles and %d groups from S3 metadata",
            len(articles),
            len(groups),
        )
        return articles, groups
    except ClientError as e:
        logger.error("Failed to retrieve batch metadata from S3: %s", e)
        return [], []
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse batch metadata from S3: %s", e)
        return [], []


def _sources_for_indices(
    indices: List[int], articles: List[Dict[str, Any]]
) -> List[ArticleSource]:
    """Build ArticleSource objects for every article index in a group."""
    return [_article_to_source(articles[i]) for i in indices if 0 <= i < len(articles)]


def build_processed_articles_from_groups(
    categorized_data: Dict[str, Any],
    original_articles: List[Dict[str, Any]],
    groups: List[List[int]],
) -> Dict[str, List[ProcessedArticle]]:
    """Convert Claude's group-summary response into ProcessedArticle objects."""
    enriched: Dict[str, List[ProcessedArticle]] = {}
    pairs = _iter_category_entries(categorized_data)

    seen_gids: set[str] = set()
    for category, entry in pairs:
        gid = entry.get("group_id") or entry.get("id")
        if not gid or gid in seen_gids:
            continue
        try:
            group_idx = int(gid.split("_", 1)[1])
        except (IndexError, ValueError):
            logger.warning("Invalid group_id %r in response", gid)
            continue
        if not 0 <= group_idx < len(groups):
            logger.warning("Group index %d out of range", group_idx)
            continue
        seen_gids.add(gid)
        indices = groups[group_idx]
        if not indices:
            continue
        primary = original_articles[indices[0]] if indices[0] < len(original_articles) else {}
        sources = _sources_for_indices(indices, original_articles)
        enriched.setdefault(category, []).append(ProcessedArticle(
            title=entry.get("title") or primary.get("title", "Untitled"),
            link=primary.get("link", ""),
            summary=entry.get("summary", ""),
            category=entry.get("category", category),
            pubdate=primary.get("pubDate", ""),
            sources=sources,
            original_description=primary.get("description"),
            comments=primary.get("comments"),
        ))

    return enriched


def _maybe_send_brief(
    categories: Dict[str, List[Any]],
    client: anthropic.Anthropic,
    to_email: str,
    source_email: str,
) -> None:
    """Generate and send the companion RSS Brief (best-effort).

    Any failure is logged and swallowed: the digest has already been sent, so the
    brief must never break the main flow or affect last_run.
    """
    try:
        article_count = sum(len(items) for items in categories.values())
        today = datetime.now().strftime("%Y-%m-%d")
        brief_html = generate_brief(
            categories,
            date=today,
            article_count=article_count,
            client=client,
        )
        if brief_html:
            send_via_ses(to_email, source_email, f"RSS Brief — {today}", brief_html)
            logger.info("Sent companion RSS Brief")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Brief generation/send failed (digest already sent): %s", exc)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613,too-many-locals
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

        # Retrieve original articles + groups from S3 if metadata_key is provided
        original_articles: List[Dict[str, Any]] = []
        groups: List[List[int]] = []
        if metadata_key:
            original_articles, groups = retrieve_batch_metadata(bucket, metadata_key)
            logger.info(
                "Retrieved %d original articles and %d groups from metadata",
                len(original_articles),
                len(groups),
            )
        else:
            logger.warning("No metadata_key provided, source attribution will be empty")

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
                        if original_articles and groups:
                            enriched_categories = build_processed_articles_from_groups(
                                categorized_data,
                                original_articles,
                                groups,
                            )
                            merge_categories(all_categories, enriched_categories)
                        else:
                            # Fallback: merge without enrichment (no source attribution)
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

        # If every request failed (e.g. all canceled due to API outage), do not send
        # an empty email and do not advance last_run — let the caller retry.
        if not all_categories and failed_requests:
            raise RuntimeError(
                f"All {len(failed_requests)} batch requests failed "
                f"(types: {set(r.result.type for r in failed_requests)}); "
                "not sending email or updating last_run"
            )

        # Load per-feed article counts for summary (optional, best-effort)
        feed_stats: Dict[str, int] = {}
        try:
            stats_response = boto3.client("s3").get_object(Bucket=bucket, Key="feed_stats.json")
            feed_stats = json.loads(stats_response["Body"].read().decode("utf-8"))
            logger.info("Loaded feed stats for %d feeds", len(feed_stats))
        except Exception:  # pylint: disable=broad-except
            logger.info("No feed stats available, skipping feed summary")

        # Format and send email (reuse existing email formatting logic)
        html_content = create_html(all_categories, feed_stats=feed_stats)
        send_via_ses(to_email, source_email, "Your Daily RSS Digest", html_content)

        # Update last_run parameter
        set_last_run(last_run_param)

        # Companion RSS Brief (best-effort; must never block the digest)
        _maybe_send_brief(all_categories, client, to_email, source_email)

        logger.info("Successfully sent email with %d categories", len(all_categories))

        return {
            "status": "success",
            "categories_count": len(all_categories),
            "failed_requests": len(failed_requests),
        }

    except Exception as e:
        logger.error("Error retrieving and sending email: %s", e, exc_info=True)
        raise
