"""Module to process RSS articles using Claude API for categorization and summarization."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import anthropic
import boto3
import pydantic
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from rss_email.json_repair import (
    repair_truncated_json,
)  # Move this import to the top level

from .models import ArticleSource

# Add to the top imports section

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Category definitions with priority order
PRIORITY_CATEGORIES = [
    "Technology",
    "AI/ML",
    "Cybersecurity",
    "Programming",
    "Science",
    "Business",
    "Politics",
    "Health",
    "Environment",
    "Entertainment",
    "Gaming",
    "Cycling",
    "Media/TV/Film",
    "Other",
]


class ProcessedArticle(BaseModel):
    """Logical article with categorization, summary, and one or more sources."""

    title: str
    link: str
    summary: str
    category: str
    pubdate: str
    sources: List[ArticleSource] = Field(default_factory=list)
    original_description: Optional[str] = None
    comments: Optional[str] = None
    model_config = {"arbitrary_types_allowed": True}


class CategorizedArticles(BaseModel):
    """Container for categorized articles."""

    categories: Dict[str, List[ProcessedArticle]]
    processing_metadata: Dict[str, Any]
    model_config = {"arbitrary_types_allowed": True}


class ClaudeRateLimiter:
    """Rate limiter for Claude API calls."""

    def __init__(self):
        self.max_tokens = int(os.environ.get("CLAUDE_MAX_TOKENS", "100000"))
        self.max_requests = int(os.environ.get("CLAUDE_MAX_REQUESTS", "10"))
        self.current_requests = 0
        self.current_tokens = 0

    def can_make_request(self, estimated_tokens: int) -> bool:
        """Check if we can make another request within limits."""
        return (
            self.current_requests < self.max_requests
            and self.current_tokens + estimated_tokens <= self.max_tokens
        )

    def record_usage(self, tokens_used: int) -> None:
        """Record API usage."""
        self.current_requests += 1
        self.current_tokens += tokens_used

    def get_usage_stats(self) -> Dict[str, int]:
        """Get current usage statistics."""
        return {
            "requests_made": self.current_requests,
            "tokens_used": self.current_tokens,
            "requests_remaining": self.max_requests - self.current_requests,
            "tokens_remaining": self.max_tokens - self.current_tokens,
        }


@pydantic.validate_call(validate_return=True)
def get_anthropic_api_key(api_key: Optional[str] = None) -> str:
    """
    Get the Anthropic API key from parameter, environment variable or Parameter Store.

    Args:
        api_key: Optional direct API key to use

    Returns:
        str: The Anthropic API key

    Raises:
        ValueError: If the API key cannot be retrieved
    """
    # First, use directly provided API key if available
    if api_key:
        return api_key

    # Second, check for environment variable
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return env_key

    # Finally, try to retrieve from Parameter Store
    try:
        parameter_name = os.environ.get("ANTHROPIC_API_KEY_PARAMETER")
        if not parameter_name:
            raise ValueError("ANTHROPIC_API_KEY_PARAMETER environment variable not set")

        ssm = boto3.client("ssm")
        response = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        return response["Parameter"]["Value"]
    except ClientError as e:
        logger.error("Error retrieving API key from parameter store: %s", e)
        raise ValueError(
            f"Could not retrieve API key from Parameter Store: {e}"
        ) from e


def estimate_tokens(articles: List[Dict[str, Any]]) -> int:
    """Estimate token count for articles."""
    # Rough estimation: 1 token per 4 characters
    total_chars = sum(
        len(str(article.get("title", "")))
        + len(str(article.get("description", "")))
        + len(str(article.get("link", "")))
        for article in articles
    )
    return total_chars // 4


def truncate_description(description: str, max_length: int = 200) -> str:
    """Truncate article description to reduce token usage."""
    if not description:
        return ""

    if len(description) <= max_length:
        return description

    # Find a good truncation point (at word boundary)
    truncated = description[:max_length]
    last_space = truncated.rfind(" ")

    if last_space > max_length * 0.8:  # If we can find a space in the last 20%
        truncated = truncated[:last_space]

    return truncated + "..."


def optimize_articles_for_claude(
    articles: List[Dict[str, Any]], max_description_length: int = 200
) -> List[Dict[str, Any]]:
    """Optimize articles for Claude processing by reducing description length."""
    optimized = []
    for article in articles:
        optimized_article = article.copy()
        description = article.get("description", "")

        # Store original description for later use
        optimized_article["original_description"] = description

        # Truncate description to reduce token usage
        optimized_article["description"] = truncate_description(
            description, max_description_length
        )

        optimized.append(optimized_article)

    return optimized


def split_articles_into_batches(
    articles: List[Dict[str, Any]], max_batch_size: int = 25
) -> List[Tuple[List[Dict[str, Any]], int]]:
    """
    Split articles into smaller batches for processing.

    Returns:
        List of tuples (batch, offset) where offset is the starting index in original list
    """
    batches = []
    for i in range(0, len(articles), max_batch_size):
        batch = articles[i:i + max_batch_size]
        batches.append((batch, i))
    return batches


def _build_group_payload(
    group_id: str,
    indices: List[int],
    articles: List[Dict[str, Any]],
    description_max_length: int = 200,
) -> Dict[str, Any]:
    """Build a single group's payload for the summarization prompt."""
    members = []
    for idx in indices:
        article = articles[idx]
        members.append(
            {
                "title": article.get("title", ""),
                "description": truncate_description(
                    article.get("description", ""), description_max_length
                ),
                "pubdate": article.get("pubDate", ""),
            }
        )
    return {"group_id": group_id, "members": members}


def create_group_summary_prompt(
    group_payloads: List[Dict[str, Any]],
) -> str:
    """Prompt Claude to assign one category + one summary per pre-formed group."""
    group_count = len(group_payloads)
    return f"""You are categorizing and summarizing {group_count} groups of RSS articles.

Each group represents ONE logical news story; some groups contain a single
article, others contain multiple articles that all cover the same event from
different feeds. You must return EXACTLY {group_count} group entries, with no
group omitted or duplicated.

INSTRUCTIONS:
1. For each group, pick the best single canonical title (prefer the clearest
   wording from the members).
2. Write a 2-3 sentence summary that covers the event, drawing on every
   member's title and description.
3. Assign exactly one category from the priority list below.

CATEGORIES (in priority order; prefer tech-related when applicable):
{", ".join(PRIORITY_CATEGORIES)}

OUTPUT FORMAT (return ONLY valid JSON, no commentary):
{{
  "categories": {{
    "category_name": [
      {{
        "group_id": "group_X",
        "title": "canonical title",
        "summary": "2-3 sentence summary",
        "category": "category_name"
      }}
    ]
  }},
  "group_count": {group_count},
  "verification": "processed_all_groups"
}}

Groups to process:
{json.dumps(group_payloads, indent=2)}
"""


def _article_to_source(article: Dict[str, Any]) -> ArticleSource:
    """Build an ArticleSource from a raw filtered article dict."""
    return ArticleSource(
        feed_name=article.get("sourceName"),
        feed_url=article.get("sourceUrl"),
        title=article.get("title", ""),
        link=article.get("link", ""),
        pubdate=article.get("pubDate", ""),
        comments=article.get("comments"),
    )


def build_groups_for_articles(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> List[List[int]]:
    """Run the grouping stage; fall back to singletons on failure."""
    # Imported lazily to avoid circular import (article_grouper imports from us)
    # pylint: disable=import-outside-toplevel
    from .article_grouper import group_articles_with_claude

    groups = group_articles_with_claude(articles, rate_limiter)
    if groups is None:
        logger.warning("Grouping failed - falling back to singleton groups")
        return [[i] for i in range(len(articles))]
    return groups


@pydantic.validate_call(
    validate_return=True, config=ConfigDict(arbitrary_types_allowed=True)
)
def process_articles_with_claude(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> Optional[CategorizedArticles]:
    """Two-stage Claude processing: cluster, then categorize + summarize."""
    if not should_process_articles(articles):
        return None

    try:
        groups = build_groups_for_articles(articles, rate_limiter)
        result = _summarize_groups_with_claude(articles, groups, rate_limiter)

        if result is not None and not isinstance(result, CategorizedArticles):
            logger.error("Expected CategorizedArticles but got %r", type(result))
            return None
        return result

    except (
        json.JSONDecodeError,
        anthropic.APIError,
        KeyError,
        IndexError,
        ValueError,
        ClientError,
    ) as e:
        logger.error("Failed to process articles with Claude: %s", e)
        logger.info("Claude processing failed, original email format will be used")
        return None


def _create_fallback_articles(articles: List[Dict[str, Any]]) -> List[ProcessedArticle]:
    """
    Create fallback ProcessedArticle objects for articles that failed Claude processing.

    This ensures articles are still displayed even if categorization fails.
    """
    fallback_articles = []
    for article in articles:
        # Use the original description as the summary
        description = article.get("description", "")
        if not description:
            description = article.get("title", "")

        # Truncate if too long
        summary = truncate_description(description, max_length=300)

        source = _article_to_source(article)
        fallback_article = ProcessedArticle(
            title=article.get("title", "Untitled"),
            link=article.get("link", ""),
            summary=summary,
            category="Uncategorized",
            pubdate=article.get("pubDate", ""),
            sources=[source],
            original_description=description,
            comments=article.get("comments"),
        )
        fallback_articles.append(fallback_article)

    return fallback_articles


def _group_fallback_articles(
    indices: List[int], articles: List[Dict[str, Any]]
) -> ProcessedArticle:
    """Create a single fallback ProcessedArticle for a group of indices."""
    members = [articles[i] for i in indices]
    primary = members[0]
    description = primary.get("description") or primary.get("title", "")
    summary = truncate_description(description, max_length=300)
    sources = [_article_to_source(article) for article in members]
    return ProcessedArticle(
        title=primary.get("title", "Untitled"),
        link=primary.get("link", ""),
        summary=summary,
        category="Uncategorized",
        pubdate=primary.get("pubDate", ""),
        sources=sources,
        original_description=description,
        comments=primary.get("comments"),
    )


def _processed_article_from_response(
    entry: Dict[str, Any],
    indices: List[int],
    articles: List[Dict[str, Any]],
    category: str,
) -> ProcessedArticle:
    """Convert one Claude category entry into a ProcessedArticle with sources."""
    members = [articles[i] for i in indices]
    primary = members[0]
    sources = [_article_to_source(article) for article in members]
    return ProcessedArticle(
        title=entry.get("title") or primary.get("title", "Untitled"),
        link=primary.get("link", ""),
        summary=entry.get("summary", ""),
        category=entry.get("category", category),
        pubdate=primary.get("pubDate", ""),
        sources=sources,
        original_description=primary.get("description"),
        comments=primary.get("comments"),
    )


def _iter_category_entries(
    categorized_data: Dict[str, Any]
) -> List[Tuple[str, Dict[str, Any]]]:
    """Yield (category_name, entry_dict) tuples from a category-summary response."""
    pairs: List[Tuple[str, Dict[str, Any]]] = []
    categories_section = categorized_data.get("categories", categorized_data)
    if isinstance(categories_section, dict):
        for category, entries in categories_section.items():
            if category in ("group_count", "article_count", "verification"):
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    pairs.append((category, entry))
    elif isinstance(categories_section, list):
        for category_obj in categories_section:
            if not isinstance(category_obj, dict):
                continue
            name = category_obj.get("name") or category_obj.get("category")
            entries = category_obj.get("articles") or category_obj.get("groups") or []
            if not name or not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    pairs.append((name, entry))
    return pairs


def _summarize_groups_with_claude(
    articles: List[Dict[str, Any]],
    groups: List[List[int]],
    rate_limiter: ClaudeRateLimiter,
) -> Optional[CategorizedArticles]:
    """Categorize + summarize each group via Claude (batched if necessary)."""
    if not groups:
        return None

    batch_size = int(os.environ.get("CLAUDE_BATCH_SIZE", "25"))
    all_categories: Dict[str, List[ProcessedArticle]] = {}
    combined_metadata: Dict[str, Any] = {
        "processed_at": datetime.now().isoformat(),
        "articles_count": len(articles),
        "groups_count": len(groups),
        "batches_processed": 0,
        "total_batches": (len(groups) + batch_size - 1) // batch_size,
        "batches_failed": 0,
        "tokens_used": 0,
        "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        "processing_time_seconds": 0,
    }
    start_time = datetime.now()

    client = _initialize_claude_client()

    for batch_start in range(0, len(groups), batch_size):
        batch_groups = groups[batch_start:batch_start + batch_size]
        payloads = []
        gid_to_indices: Dict[str, List[int]] = {}
        for offset, indices in enumerate(batch_groups):
            gid = f"group_{batch_start + offset}"
            payloads.append(_build_group_payload(gid, indices, articles))
            gid_to_indices[gid] = indices

        prompt = create_group_summary_prompt(payloads)

        if not rate_limiter.can_make_request(len(prompt) // 4):
            logger.warning("Rate limit reached before group batch starting at %d", batch_start)
            combined_metadata["batches_failed"] += 1
            for indices in batch_groups:
                all_categories.setdefault("Uncategorized", []).append(
                    _group_fallback_articles(indices, articles)
                )
            continue

        categorized_data, usage_stats = _call_claude_with_prompt(
            client,
            prompt,
            rate_limiter,
            unit_count=len(payloads),
            unit_label="groups",
        )

        if not categorized_data or usage_stats is None:
            combined_metadata["batches_failed"] += 1
            for indices in batch_groups:
                all_categories.setdefault("Uncategorized", []).append(
                    _group_fallback_articles(indices, articles)
                )
            continue

        combined_metadata["batches_processed"] += 1
        combined_metadata["tokens_used"] += usage_stats.get("tokens_used", 0)

        seen_gids: set[str] = set()
        for category, entry in _iter_category_entries(categorized_data):
            gid = entry.get("group_id") or entry.get("id")
            if not gid or gid not in gid_to_indices or gid in seen_gids:
                continue
            seen_gids.add(gid)
            indices = gid_to_indices[gid]
            processed = _processed_article_from_response(
                entry, indices, articles, category
            )
            all_categories.setdefault(category, []).append(processed)

        # Any group not echoed by Claude gets a fallback so it's not dropped
        for gid, indices in gid_to_indices.items():
            if gid not in seen_gids:
                logger.warning("Group %s missing from Claude response; using fallback", gid)
                all_categories.setdefault("Uncategorized", []).append(
                    _group_fallback_articles(indices, articles)
                )

    combined_metadata["processing_time_seconds"] = (
        datetime.now() - start_time
    ).total_seconds()

    if not all_categories:
        logger.error("No groups were successfully processed")
        return None

    return CategorizedArticles(
        categories=all_categories,
        processing_metadata=combined_metadata,
    )


def should_process_articles(articles: List[Dict[str, Any]]) -> bool:
    """Check if articles should be processed."""
    if not articles:
        logger.info("No articles to process")
        return False

    if os.environ.get("CLAUDE_ENABLED", "true").lower() != "true":
        logger.info("Claude processing is disabled")
        return False

    return True


def _initialize_claude_client() -> anthropic.Anthropic:
    """Initialize the Claude API client."""
    api_key = get_anthropic_api_key()
    return anthropic.Anthropic(api_key=api_key)


def _call_claude_with_prompt(
    client: anthropic.Anthropic,
    prompt: str,
    rate_limiter: ClaudeRateLimiter,
    *,
    unit_count: int,
    unit_label: str = "items",
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Call the Claude API with a pre-built prompt and parse the JSON response."""
    try:
        estimated_tokens = len(prompt) // 4
        logger.info("Estimated input tokens: %s", estimated_tokens)

        # Get timeout from environment variable or default to 120 seconds (2 minutes)
        api_timeout = int(os.environ.get("CLAUDE_API_TIMEOUT", "120"))

        # Use environment variable CLAUDE_MODEL without default
        model_name = os.environ.get("CLAUDE_MODEL")
        if not model_name:
            logger.warning("CLAUDE_MODEL not set, using default model")
            model_name = "claude-3-7-sonnet-latest"  # Updated default model

        # Debug to check what's being used
        logger.debug("Using environment model: %s", model_name)

        # Set max_tokens based on model to avoid errors
        max_tokens = _get_max_tokens_for_model(model_name)

        logger.info("Using model %s with max_tokens=%s", model_name, max_tokens)

        start_time = datetime.now()
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=0.3,  # Use 0.0 for more deterministic JSON responses
            messages=[{"role": "user", "content": prompt}],
            timeout=api_timeout,  # Set API request timeout
        )

        # Parse response text
        response_text = response.content[0].text.strip()

        # Check for empty response
        if not response_text:
            logger.error("Received empty response from Claude API")
            return None, None

        # Log response size for monitoring
        response_size = len(response_text)
        logger.info("Received response of %d characters", response_size)

        # Warn if response seems suspiciously short
        if response_size < 500:
            logger.warning(
                "Response is suspiciously short (%d chars) for %d %s",
                response_size,
                unit_count,
                unit_label,
            )

        # Try to extract valid JSON with error handling for truncation
        categorized_data = None
        try:
            # First attempt to parse the entire response as JSON
            categorized_data = json.loads(response_text)
        except json.JSONDecodeError as e:
            # Enhanced logging for debugging
            logger.warning(
                "JSON parse error: %s. Response length: %d chars. Error position: char %s",
                e,
                len(response_text),
                getattr(e, 'pos', 'unknown')
            )

            # Log the problematic response for debugging
            logger.debug("Problematic JSON response: %s", response_text[:1000])

            # Try to repair the JSON (using the top-level import)
            categorized_data = repair_truncated_json(response_text)

            if categorized_data is None:
                logger.error(
                    "Failed to repair truncated JSON using repair_truncated_json"
                )
                # Instead of returning None, let's try a more aggressive approach
                # Look for the actual JSON object within the response
                try:
                    # Find first opening brace
                    start = response_text.find("{")
                    if start == -1:
                        logger.error("No JSON object found in response")
                        return None, None

                    # Find last closing brace
                    end = response_text.rfind("}")
                    if end == -1:
                        logger.error("No closing brace found in response")
                        return None, None

                    # Extract potential JSON substring
                    json_substring = response_text[start:end + 1]
                    categorized_data = json.loads(json_substring)
                    logger.info("Successfully extracted JSON from response substring")
                except (json.JSONDecodeError, ValueError) as extract_error:
                    logger.error(
                        "Failed to extract JSON from response: %s", extract_error
                    )
                    return None, None

        if not categorized_data:
            logger.error("Could not extract valid JSON from response")
            return None, None

        # Process usage metrics
        tokens_used = response.usage.input_tokens + response.usage.output_tokens
        rate_limiter.record_usage(tokens_used)

        processing_time = (datetime.now() - start_time).total_seconds()
        usage_stats = {
            "processed_at": datetime.now().isoformat(),
            unit_label + "_count": unit_count,
            "tokens_used": tokens_used,
            "model": model_name,
            "processing_time_seconds": processing_time,
        }

        _log_api_success(usage_stats, rate_limiter, unit_count, unit_label)

        return categorized_data, usage_stats

    except json.JSONDecodeError as e:
        logger.error("Failed to parse Claude response: %s", e)
        return None, None
    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        return None, None


def _get_max_tokens_for_model(model_name: str) -> int:
    """Get the maximum token limit for a given Claude model."""
    if "claude-sonnet-4" in model_name:
        return 8000  # Conservative limit to prevent truncation

    if "claude-3-7-sonnet" in model_name:
        return 8000  # Conservative limit to prevent truncation

    if "claude-3-5-sonnet" in model_name:
        return 4000  # More conservative to avoid truncation issues

    if "claude-3-opus" in model_name:
        return 8000  # Conservative limit

    if "claude-3-haiku" in model_name:
        return 4000  # Conservative limit for haiku models

    if "claude-3-5-haiku" in model_name:
        return 4000  # Conservative limit for haiku models

    if "claude-haiku-4" in model_name:
        return 8192  # Increased to prevent truncation - supports up to 30 articles

    if "claude-2" in model_name:
        return 8000  # Conservative even for Claude 2

    if "claude-sonnet" in model_name:
        return 4000

    return 2000  # Very conservative default


def _log_api_success(
    usage_stats: Dict[str, Any],
    rate_limiter: ClaudeRateLimiter,
    unit_count: int,
    unit_label: str,
) -> None:
    """Log API success metrics."""
    logger.info(
        {
            "event": "claude_api_success",
            "model": os.environ.get("CLAUDE_MODEL"),
            f"{unit_label}_processed": unit_count,
            "tokens_used": usage_stats["tokens_used"],
            "processing_time_seconds": usage_stats["processing_time_seconds"],
            "usage_stats": rate_limiter.get_usage_stats(),
        }
    )


def group_articles_by_priority(
    categorized_articles: CategorizedArticles,
) -> List[Tuple[str, List[ProcessedArticle]]]:
    """Group articles by category priority order."""
    ordered_categories = []

    # First add categories in priority order
    for category in PRIORITY_CATEGORIES:
        if category in categorized_articles.categories:
            ordered_categories.append(
                (category, categorized_articles.categories[category])
            )

    # Then add any categories not in the priority list
    for category, articles in categorized_articles.categories.items():
        if category not in PRIORITY_CATEGORIES:
            ordered_categories.append((category, articles))

    return ordered_categories
