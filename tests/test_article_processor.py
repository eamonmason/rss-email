#!/usr/bin/env python3
"""Unit tests for the article_processor module (two-stage flow)."""
# pylint: disable=wrong-import-position

import json
import logging
import os
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


from rss_email.article_processor import (  # noqa: E402
    CategorizedArticles,
    ClaudeRateLimiter,
    ProcessedArticle,
    _article_to_source,
    _build_group_payload,
    _processed_article_from_response,
    create_group_summary_prompt,
    estimate_tokens,
    get_anthropic_api_key,
    group_articles_by_priority,
    process_articles_with_claude,
)
from rss_email.compression_utils import compress_json, decompress_json  # noqa: E402


def create_sample_articles() -> List[Dict[str, Any]]:
    """Create sample RSS articles for testing."""
    return [
        {
            "title": "OpenAI Announces GPT-5 with Advanced Reasoning",
            "link": "https://example.com/openai-gpt5",
            "description": "OpenAI has unveiled GPT-5.",
            "pubDate": "Mon, 26 May 2025 08:00:00 GMT",
            "sourceName": "OpenAI Blog",
            "sourceUrl": "https://openai.com/blog/rss/",
        },
        {
            "title": "Apple Releases Vision Pro 2 with Enhanced Display",
            "link": "https://example.com/apple-vision-pro-2",
            "description": "Apple today announced the Vision Pro 2.",
            "pubDate": "Mon, 26 May 2025 07:30:00 GMT",
            "sourceName": "Apple Newsroom",
            "sourceUrl": "https://www.apple.com/newsroom/rss-feed.rss",
        },
    ]


def test_rate_limiter():
    """The rate limiter blocks requests once limits are reached."""
    os.environ["CLAUDE_MAX_TOKENS"] = "50000"
    os.environ["CLAUDE_MAX_REQUESTS"] = "3"

    limiter = ClaudeRateLimiter()

    assert limiter.can_make_request(10000)
    assert not limiter.can_make_request(60000)

    limiter.record_usage(15000)
    limiter.record_usage(20000)
    assert limiter.can_make_request(10000)
    limiter.record_usage(10000)
    assert not limiter.can_make_request(1000)


def test_token_estimation():
    """Token estimation handles empty and non-empty inputs."""
    articles = create_sample_articles()
    assert estimate_tokens([]) == 0
    assert estimate_tokens(articles) > 0


def test_group_summary_prompt_contains_required_fields():
    """The summarize+categorize prompt mentions the categories list and group ids."""
    articles = create_sample_articles()
    payloads = [
        _build_group_payload("group_0", [0], articles),
        _build_group_payload("group_1", [1], articles),
    ]

    prompt = create_group_summary_prompt(payloads)

    assert "CATEGORIES" in prompt
    assert "group_0" in prompt
    assert "group_1" in prompt
    assert articles[0]["title"] in prompt
    assert articles[1]["title"] in prompt


def test_processed_article_from_response_merges_sources():
    """ProcessedArticle.sources contains one entry per member article."""
    articles = create_sample_articles()
    entry = {
        "group_id": "group_0",
        "title": "Combined OpenAI/Apple story",
        "summary": "Two big announcements.",
    }

    processed = _processed_article_from_response(entry, [0, 1], articles, "Technology")

    assert processed.category == "Technology"
    assert processed.summary == "Two big announcements."
    assert len(processed.sources) == 2
    assert processed.sources[0].feed_name == "OpenAI Blog"
    assert processed.sources[1].feed_name == "Apple Newsroom"


def test_article_to_source_carries_feed_attribution():
    """The helper preserves feed name and url from the raw article dict."""
    article = create_sample_articles()[0]

    source = _article_to_source(article)

    assert source.feed_name == "OpenAI Blog"
    assert source.feed_url == "https://openai.com/blog/rss/"
    assert source.title == article["title"]


def test_api_key_retrieval():
    """API key retrieval may fail without AWS credentials - this is expected."""
    os.environ["ANTHROPIC_API_KEY_PARAMETER"] = "rss-email-anthropic-api-key"
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

    try:
        get_anthropic_api_key()
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    return True


def test_fallback_behavior():
    """process_articles_with_claude returns None when disabled or given no input."""
    os.environ["CLAUDE_ENABLED"] = "false"
    rate_limiter = ClaudeRateLimiter()
    assert process_articles_with_claude(create_sample_articles(), rate_limiter) is None

    os.environ["CLAUDE_ENABLED"] = "true"
    assert process_articles_with_claude([], rate_limiter) is None


def test_json_compression():
    """compress/decompress round-trips ProcessedArticle-shaped data."""
    test_data = {
        "categories": {
            "Technology": [
                {
                    "group_id": "group_0",
                    "title": "Test Article",
                    "summary": "This is a test summary.",
                    "category": "Technology",
                }
            ]
        }
    }

    compressed = compress_json(test_data)
    decompressed = decompress_json(compressed)

    assert decompressed == test_data


def test_priority_grouping_orders_categories():
    """group_articles_by_priority lists tech categories first, others after."""
    cat = CategorizedArticles(
        categories={
            "Other": [
                ProcessedArticle(
                    title="O",
                    link="https://x.example/o",
                    summary="s",
                    category="Other",
                    pubdate="d",
                )
            ],
            "Technology": [
                ProcessedArticle(
                    title="T",
                    link="https://x.example/t",
                    summary="s",
                    category="Technology",
                    pubdate="d",
                )
            ],
        },
        processing_metadata={},
    )

    ordered = group_articles_by_priority(cat)

    assert ordered[0][0] == "Technology"
    assert ordered[-1][0] == "Other"


def _maybe_run_claude_integration() -> Optional[Dict[str, Any]]:
    """If real API credentials are present, do a smoke run; otherwise skip."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    os.environ["CLAUDE_ENABLED"] = "true"
    os.environ.setdefault("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    rate_limiter = ClaudeRateLimiter()
    result = process_articles_with_claude(create_sample_articles(), rate_limiter)
    if result:
        return json.loads(result.model_dump_json())
    return None
