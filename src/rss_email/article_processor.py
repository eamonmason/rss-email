"""Module to process RSS articles using Claude API for categorization and summarization."""

from __future__ import annotations

# Add these imports at the top
import base64
import gzip
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
    """Processed article with categorization and summary."""

    title: str
    link: str
    summary: str
    category: str
    pubdate: str
    related_articles: List[str] = Field(default_factory=list)
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
        logger.error(
            "Error retrieving parameter '%s' from parameter store: %s",
            parameter_name,
            e
        )
        raise ValueError(
            f"Could not retrieve parameter '{parameter_name}' from Parameter Store: {e}"
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
) -> List[List[Dict[str, Any]]]:
    """Split articles into smaller batches for processing."""
    batches = []
    for i in range(0, len(articles), max_batch_size):
        batch = articles[i:i + max_batch_size]
        batches.append(batch)
    return batches


# Add these utility functions
def compress_json(data: Dict[str, Any]) -> str:
    """Compress JSON data to base64-encoded gzipped string."""
    json_str = json.dumps(data)
    compressed = gzip.compress(json_str.encode("utf-8"))
    return base64.b64encode(compressed).decode("ascii")


def decompress_json(compressed_str: str) -> Dict[str, Any]:
    """Decompress base64-encoded gzipped JSON string."""
    decoded = base64.b64decode(compressed_str)
    decompressed = gzip.decompress(decoded)
    return json.loads(decompressed.decode("utf-8"))


def create_categorization_prompt(articles: List[Dict[str, Any]]) -> str:
    """Create the prompt for Claude to categorize and summarize articles."""
    # Optimize articles for processing
    optimized_articles = optimize_articles_for_claude(articles)
    articles_json = []
    for idx, article in enumerate(optimized_articles):
        articles_json.append(
            {
                "id": f"article_{idx}",
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "link": article.get("link", ""),
                "pubdate": article.get("pubDate", ""),
            }
        )

    prompt = f"""You are processing {len(articles_json)} RSS articles.
                 You MUST return ALL {len(articles_json)} articles in your response.

CRITICAL REQUIREMENTS:
- Input: {len(articles_json)} articles
- Output: EXACTLY {len(articles_json)} articles (no more, no less)
- Every single article from the input must appear in your output
- If you're unsure about categorization, use your best judgment but DO NOT omit any articles

PROCESSING INSTRUCTIONS:
1. Read through ALL articles first to get complete context
2. Categorize each article using the priority categories below
3. Create 2-3 sentence summaries for each article
4. Identify related articles that cover similar topics
5. VERIFY your output contains all {len(articles_json)} articles before responding

CATEGORIES (in priority order - prefer tech-related when applicable):
{", ".join(PRIORITY_CATEGORIES)}

Return a JSON response in this exact format (before compression):

YOU MUST FOLLOW THESE STRICT FORMAT RULES:
- Return ONLY valid JSON with no explanations or additional text
- Use proper JSON syntax with commas between all properties and array elements
- Do not include any text before or after the JSON
- Ensure all strings are properly quoted with double quotes
- Ensure all commas are properly placed between properties and array elements
- Test your JSON mentally for syntax errors before responding

Required JSON structure (compress before returning):
{{
  "categories": {{
    "category_name": [
      {{
        "id": "article_X",
        "title": "original title",
        "link": "original link",
        "summary": "brief 1-2 sentence summary",
        "category": "category_name",
        "pubdate": "original pubdate",
        "related_articles": ["article_Y", "article_Z"]
      }}
    ]
  }},
  "article_count": {len(articles_json)},
  "verification": "processed_all_articles"
}}

  }}
}}

Important:
- Every article must appear in exactly one category
- All original articles must be included in the response.
- The count of articles in the response must match the input count.
- Preserve all original article data (title, link, pubdate)
- Group similar articles using the related_articles field
- Prioritize tech-related categories over entertainment/lifestyle categories

FINAL CHECK: Before responding, count your articles and confirm you have exactly {len(articles_json)} articles.

Articles to process:
{json.dumps(articles_json, indent=2)}
"""
    return prompt


@pydantic.validate_call(
    validate_return=True, config=ConfigDict(arbitrary_types_allowed=True)
)
def process_articles_with_claude(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> Optional[CategorizedArticles]:
    """Process articles using Claude API for categorization and summarization."""
    result = None

    # Early validation check
    if not should_process_articles(articles):
        return None

    try:
        # Check if we need to split into batches for large article sets
        if len(articles) > 15:
            logger.info(
                "Large article set detected (%d articles), processing in batches",
                len(articles),
            )
            result = _process_articles_in_batches(articles, rate_limiter)
        else:
            # Initialize client and process articles normally
            result = _process_with_claude_client(articles, rate_limiter)

        # Validate the result is properly formatted before returning
        if result is not None and not isinstance(result, CategorizedArticles):
            logger.error("Expected CategorizedArticles but got %r", type(result))
            return None

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

    return result


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

        fallback_article = ProcessedArticle(
            title=article.get("title", "Untitled"),
            link=article.get("link", ""),
            summary=summary,
            category="Uncategorized",
            pubdate=article.get("pubDate", ""),
            related_articles=[],
            original_description=description,
        )
        fallback_articles.append(fallback_article)

    return fallback_articles


def _process_articles_in_batches(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> Optional[CategorizedArticles]:
    """Process articles in smaller batches to avoid token limits."""
    # Get batch size from environment or use default
    batch_size = int(os.environ.get("CLAUDE_BATCH_SIZE", "25"))
    batches = split_articles_into_batches(articles, max_batch_size=batch_size)

    all_categories = {}
    combined_metadata = {
        "processed_at": datetime.now().isoformat(),
        "articles_count": len(articles),
        "batches_processed": 0,
        "total_batches": len(batches),
        "batches_failed": 0,
        "tokens_used": 0,
        "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
        "processing_time_seconds": 0,
    }

    start_time = datetime.now()

    for batch_idx, batch in enumerate(batches):
        logger.info(
            "Processing batch %d/%d with %d articles",
            batch_idx + 1,
            len(batches),
            len(batch),
        )

        # Process this batch
        batch_result = _process_with_claude_client(batch, rate_limiter)

        if batch_result is None:
            logger.warning(
                "Batch %d failed to process, using fallback display for %d articles",
                batch_idx + 1,
                len(batch),
            )
            combined_metadata["batches_failed"] += 1
            # Create fallback articles for failed batch
            fallback_articles = _create_fallback_articles(batch)
            if "Uncategorized" not in all_categories:
                all_categories["Uncategorized"] = []
            all_categories["Uncategorized"].extend(fallback_articles)
            continue

        # Merge results
        for category, category_articles in batch_result.categories.items():
            if category not in all_categories:
                all_categories[category] = []
            all_categories[category].extend(category_articles)

        # Update metadata
        combined_metadata["batches_processed"] += 1
        if "tokens_used" in batch_result.processing_metadata:
            combined_metadata["tokens_used"] += batch_result.processing_metadata[
                "tokens_used"
            ]

    combined_metadata["processing_time_seconds"] = (
        datetime.now() - start_time
    ).total_seconds()

    if not all_categories:
        logger.error("No articles were successfully processed in any batch")
        return None

    return CategorizedArticles(
        categories=all_categories,
        processing_metadata=combined_metadata,
    )


def _process_with_claude_client(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> Optional[CategorizedArticles]:
    """Process articles with initialized Claude client."""
    # Initialize client and check rate limits
    client = _initialize_claude_client()
    if not _check_rate_limits(articles, rate_limiter):
        return None

    # Process with Claude API
    categorized_data, usage_stats = _call_claude_api(client, articles, rate_limiter)
    if not categorized_data or usage_stats is None:
        return None

    # Convert to structured format
    return _create_categorized_articles(categorized_data, articles, usage_stats)


def _log_processing_error(error: Exception) -> None:
    """Log specific error types with appropriate messages."""
    if isinstance(error, json.JSONDecodeError):
        logger.error("Failed to parse Claude response: %s", error)
    elif isinstance(error, anthropic.APIError):
        logger.error("Anthropic API error: %s", error)
    elif isinstance(error, (KeyError, IndexError, ValueError)):
        logger.error("Error processing API response structure: %s", error)
    elif isinstance(error, ClientError):
        logger.error("AWS client error: %s", error)
    else:
        logger.error("Unexpected error processing articles: %s", error)


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


def _check_rate_limits(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> bool:
    """Check if the request is within rate limits."""
    estimated_tokens = estimate_tokens(articles)
    if not rate_limiter.can_make_request(estimated_tokens):
        logger.warning(
            "Rate limit would be exceeded. Estimated tokens: %s, Current usage: %s",
            estimated_tokens,
            rate_limiter.get_usage_stats(),
        )
        return False
    return True


def _call_claude_api(
    client: anthropic.Anthropic,
    articles: List[Dict[str, Any]],
    rate_limiter: ClaudeRateLimiter,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Call the Claude API and process the response."""
    try:
        # Create prompt and call API
        prompt = create_categorization_prompt(articles)
        estimated_tokens = estimate_tokens(articles)
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
                "Response is suspiciously short (%d chars) for %d articles",
                response_size,
                len(articles)
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
            "articles_count": len(articles),
            "tokens_used": tokens_used,
            "model": model_name,
            "processing_time_seconds": processing_time,
        }

        _log_api_success(usage_stats, rate_limiter, articles)

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
    articles: List[Dict[str, Any]],
) -> None:
    """Log API success metrics."""
    logger.info(
        {
            "event": "claude_api_success",
            "model": os.environ.get("CLAUDE_MODEL"),
            "articles_processed": len(articles),
            "tokens_used": usage_stats["tokens_used"],
            "processing_time_seconds": usage_stats["processing_time_seconds"],
            "usage_stats": rate_limiter.get_usage_stats(),
        }
    )


def _process_article_entry(article, articles, category):
    """Process a single article entry."""
    article_id = article["id"]
    idx = int(article_id.split("_")[1])
    if idx < len(articles):
        original_desc = articles[idx].get("description", "")
        comments = articles[idx].get("comments", None)
    else:
        logger.warning("Article index %s out of range. Using empty description.", idx)
        original_desc = ""
        comments = None

    return ProcessedArticle(
        title=article["title"],
        link=article["link"],
        summary=article["summary"],
        category=category,
        pubdate=article["pubdate"],
        related_articles=article.get("related_articles", []),
        original_description=original_desc,
        comments=comments,
    )


def _process_dictionary_categories(categories_data, articles):
    """Process categories in dictionary format."""
    processed_categories = {}
    for category, category_data in categories_data.items():
        # Skip metadata fields that are not actual categories
        if category in ("article_count", "verification"):
            continue
        articles_data = category_data
        # Handle both possible structures for articles
        if isinstance(category_data, dict) and "articles" in category_data:
            articles_data = category_data["articles"]

        # Ensure articles_data is actually iterable
        if not isinstance(articles_data, (list, tuple)):
            logger.error(
                "Expected list of articles but got %s for category %s",
                type(articles_data),
                category,
            )
            continue

        processed_articles = []
        for article in articles_data:
            processed_article = _process_article_entry(article, articles, category)
            processed_articles.append(processed_article)

        if processed_articles:
            processed_categories[category] = processed_articles
    return processed_categories


def _process_list_categories(categories_data, articles):
    """Process categories in list format."""
    processed_categories = {}
    for category_obj in categories_data:
        if "name" in category_obj and "articles" in category_obj:
            category = category_obj["name"]
            articles_list = category_obj["articles"]

            # Ensure articles_list is actually iterable
            if not isinstance(articles_list, (list, tuple)):
                logger.error(
                    "Expected list of articles but got %s for category %s",
                    type(articles_list),
                    category,
                )
                continue

            processed_articles = []
            for article in articles_list:
                processed_article = _process_article_entry(article, articles, category)
                processed_articles.append(processed_article)

            if processed_articles:
                processed_categories[category] = processed_articles
    return processed_categories


def _create_categorized_articles(
    categorized_data: Dict[str, Any],
    articles: List[Dict[str, Any]],
    usage_stats: Dict[str, Any],
) -> CategorizedArticles:
    """Convert categorized data to structured format."""
    try:
        processed_categories = {}

        # Handle both possible JSON structures from Claude
        if "categories" in categorized_data:
            categories_data = categorized_data["categories"]

            # Handle structure where categories are keys
            if isinstance(categories_data, dict):
                processed_categories = _process_dictionary_categories(
                    categories_data, articles
                )
            # Handle structure where categories is a list of category objects
            elif isinstance(categories_data, list):
                processed_categories = _process_list_categories(
                    categories_data, articles
                )
        else:
            # Handle case where categories are at the top level
            # Filter out metadata fields first
            categories_data = {
                k: v
                for k, v in categorized_data.items()
                if k not in ("article_count", "verification")
            }
            processed_categories = _process_dictionary_categories(
                categories_data, articles
            )

        return CategorizedArticles(
            categories=processed_categories,
            processing_metadata=usage_stats,
        )
    except (ValueError, KeyError, TypeError, IndexError) as e:
        logger.error("Error creating categorized articles: %s", e)
        logger.warning(
            "Using fallback display for %d articles due to categorization error",
            len(articles),
        )
        # Create fallback articles so they're still displayed
        fallback_articles = _create_fallback_articles(articles)
        return CategorizedArticles(
            categories={"Uncategorized": fallback_articles},
            processing_metadata=usage_stats,
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
