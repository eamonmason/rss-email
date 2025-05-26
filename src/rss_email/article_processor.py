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
        self.max_requests = int(os.environ.get("CLAUDE_MAX_REQUESTS", "5"))
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
        logger.error("Error retrieving Anthropic API key: %s", e)
        raise ValueError(f"Could not retrieve API key from Parameter Store: {e}")


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


def create_categorization_prompt(articles: List[Dict[str, Any]]) -> str:
    """Create the prompt for Claude to categorize and summarize articles."""
    articles_json = []
    for idx, article in enumerate(articles):
        articles_json.append(
            {
                "id": f"article_{idx}",
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "link": article.get("link", ""),
                "pubdate": article.get("pubDate", ""),
            }
        )

    prompt = f"""Analyze these RSS articles and categorize them intelligently. For each article:
1. Assign it to the most appropriate category from the list below
2. Create a concise 2-3 sentence summary that captures the key information
3. Identify related articles that cover similar topics or events

Categories to use (in priority order - prefer tech-related categories when applicable):
{", ".join(PRIORITY_CATEGORIES)}

Return a JSON response in this exact format:
{{
  "categories": {{
    "category_name": {{
      "articles": [
        {{
          "id": "article_X",
          "title": "original title",
          "link": "original link",
          "summary": "2-3 sentence summary",
          "category": "category_name",
          "pubdate": "original pubdate",
          "related_articles": ["article_Y", "article_Z"]
        }}
      ]
    }}
  }}
}}

Important:
- Every article must appear in exactly one category
- Preserve all original article data (title, link, pubdate)
- Group similar articles using the related_articles field
- Prioritize tech-related categories over entertainment/lifestyle categories

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
        # Initialize client and process articles
        result = _process_with_claude_client(articles, rate_limiter)
    except (
        json.JSONDecodeError,
        anthropic.APIError,
        KeyError,
        IndexError,
        ValueError,
        ClientError,
    ) as e:
        _log_processing_error(e)

    return result


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

        # Get model name
        model_name = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

        # Set max_tokens based on model to avoid errors
        max_tokens = 4000  # Default conservative value
        if "claude-3-5-sonnet" in model_name:
            max_tokens = 8000  # Safe value below the 8,192 limit
        elif "claude-3-opus" in model_name:
            max_tokens = 30000
        elif "claude-3-haiku" in model_name:
            max_tokens = 4000
        elif "claude-3-5-haiku" in model_name:
            max_tokens = 4000
        elif "claude-2" in model_name:
            max_tokens = 100000  # Claude 2 had very high limits
        elif "claude-sonnet" in model_name:
            max_tokens = 8000

        logger.info(f"Using model {model_name} with max_tokens={max_tokens}")

        start_time = datetime.now()
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
            timeout=api_timeout,  # Set API request timeout
        )

        # Parse response using the extraction function
        response_text = response.content[0].text
        categorized_data = _extract_json_from_text(response_text)

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


def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Extract valid JSON from text that might contain additional content."""
    import re

    # Improved debugging - log beginning of response
    logger.debug(
        "Response starts with: %s", text[:500] + "..." if len(text) > 500 else text
    )

    # First try: direct parsing in case the response is clean JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "categories" in parsed:
            logger.info("Successfully parsed complete JSON response directly")
            return parsed
    except json.JSONDecodeError:
        pass  # Continue with extraction methods

    # Look for JSON between triple backticks (common Claude formatting)
    backtick_json = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if backtick_json:
        try:
            candidate = backtick_json.group(1).strip()
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and "categories" in parsed:
                logger.info("Successfully extracted JSON from code block")
                return parsed
        except json.JSONDecodeError:
            pass

    # Try to find the most complete JSON object
    # Look for objects with the expected structure
    candidates = []

    # Pattern to find JSON objects
    json_pattern = r"\{(?:[^{}]|(?R))*\}"
    try:
        # Use a more lenient approach with regex
        import regex  # This requires the 'regex' package which supports recursive patterns

        matches = regex.findall(json_pattern, text)
        for match in sorted(matches, key=len, reverse=True):
            try:
                parsed = json.loads(match)
                if isinstance(parsed, dict) and "categories" in parsed:
                    candidates.append(parsed)
            except json.JSONDecodeError:
                continue
    except (ImportError, Exception) as e:
        logger.debug(f"Advanced regex failed: {e}, falling back to basic approach")

        # Fallback approach if regex module not available
        # Try to find JSON by bracket balancing
        start_positions = [i for i, c in enumerate(text) if c == "{"]
        for start in start_positions:
            bracket_count = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    bracket_count += 1
                elif text[i] == "}":
                    bracket_count -= 1
                    if bracket_count == 0:
                        # Found potential complete JSON
                        potential = text[start : i + 1]
                        try:
                            parsed = json.loads(potential)
                            if isinstance(parsed, dict) and "categories" in parsed:
                                candidates.append(parsed)
                                break
                        except json.JSONDecodeError:
                            continue
                        break

    if candidates:
        # Return the first valid candidate
        logger.info("Successfully extracted JSON using bracket balancing")
        return candidates[0]

    # Last resort: try to fix common JSON formatting issues
    cleaned_text = text

    # Try to find a section that looks like JSON by searching for "categories":
    categories_pos = cleaned_text.find('"categories"')
    if categories_pos > 0:
        # Look for opening brace before "categories"
        opening_brace_pos = cleaned_text.rfind("{", 0, categories_pos)
        if opening_brace_pos >= 0:
            # Look for closing brace that would complete this object
            cleaned_text = cleaned_text[opening_brace_pos:]
            closing_braces = [
                i + opening_brace_pos for i, c in enumerate(cleaned_text) if c == "}"
            ]

            # Try each potential closing position
            for closing_pos in closing_braces:
                potential = cleaned_text[: closing_pos + 1]
                try:
                    parsed = json.loads(potential)
                    if isinstance(parsed, dict) and "categories" in parsed:
                        logger.info(
                            "Successfully extracted JSON using categories marker"
                        )
                        return parsed
                except json.JSONDecodeError:
                    continue

    # If we got here, extraction failed
    logger.error("Failed to extract valid JSON from Claude response")
    return None


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


def _create_categorized_articles(
    categorized_data: Dict[str, Any],
    articles: List[Dict[str, Any]],
    usage_stats: Dict[str, Any],
) -> CategorizedArticles:
    """Convert categorized data to structured format."""
    processed_categories = {}

    for category, category_data in categorized_data["categories"].items():
        processed_articles = []
        for article in category_data["articles"]:
            # Find original description
            article_id = article["id"]
            idx = int(article_id.split("_")[1])
            original_desc = articles[idx].get("description", "")

            processed_article = ProcessedArticle(
                title=article["title"],
                link=article["link"],
                summary=article["summary"],
                category=category,
                pubdate=article["pubdate"],
                related_articles=article.get("related_articles", []),
                original_description=original_desc,
            )
            processed_articles.append(processed_article)

        if processed_articles:
            processed_categories[category] = processed_articles

    return CategorizedArticles(
        categories=processed_categories,
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
