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
def get_anthropic_api_key() -> str:
    """Get the Anthropic API key from parameter store."""
    parameter_name = os.environ["ANTHROPIC_API_KEY_PARAMETER"]
    try:
        ssm = boto3.client("ssm")
        parameter = ssm.get_parameter(Name=parameter_name, WithDecryption=True)
        return parameter["Parameter"]["Value"]
    except ClientError as e:
        logger.error(f"Error retrieving Anthropic API key: {e}")
        raise


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
    if not articles:
        logger.info("No articles to process")
        return None

    # Check if Claude is enabled
    if os.environ.get("CLAUDE_ENABLED", "true").lower() != "true":
        logger.info("Claude processing is disabled")
        return None

    try:
        # Get API key and initialize client
        api_key = get_anthropic_api_key()
        client = anthropic.Anthropic(api_key=api_key)

        # Estimate tokens and check rate limits
        estimated_tokens = estimate_tokens(articles)
        if not rate_limiter.can_make_request(estimated_tokens):
            logger.warning(
                f"Rate limit would be exceeded. Estimated tokens: {estimated_tokens}, "
                f"Current usage: {rate_limiter.get_usage_stats()}"
            )
            return None

        # Create prompt
        prompt = create_categorization_prompt(articles)

        # Make API call
        start_time = datetime.now()
        response = client.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022"),
            max_tokens=4000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )

        # Parse response
        response_text = response.content[0].text
        categorized_data = json.loads(response_text)

        # Record usage
        tokens_used = response.usage.input_tokens + response.usage.output_tokens
        rate_limiter.record_usage(tokens_used)

        # Log metrics
        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(
            {
                "event": "claude_api_success",
                "model": os.environ.get("CLAUDE_MODEL"),
                "articles_processed": len(articles),
                "tokens_used": tokens_used,
                "processing_time_seconds": processing_time,
                "usage_stats": rate_limiter.get_usage_stats(),
            }
        )

        # Convert to structured format
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
            processing_metadata={
                "processed_at": datetime.now().isoformat(),
                "articles_count": len(articles),
                "tokens_used": tokens_used,
                "processing_time_seconds": processing_time,
            },
        )

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response: {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing articles with Claude: {e}")
        return None


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
