#!/usr/bin/env python3
"""
Test script for the article_processor module.
This script allows comprehensive testing of the Claude integration locally.
Also includes specific tests for the _create_categorized_articles function to handle error cases.
"""
# pylint: disable=wrong-import-position

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


from rss_email.article_processor import (  # noqa: E402
    ClaudeRateLimiter,
    _create_categorized_articles,
    create_categorization_prompt,
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
            "description": (
                "OpenAI has unveiled GPT-5, featuring breakthrough advances in logical reasoning "
                "and mathematical problem-solving. The new model demonstrates significant "
                "improvements in complex task handling and reduced hallucinations."
            ),
            "pubDate": "Mon, 26 May 2025 08:00:00 GMT",
        },
        {
            "title": "Apple Releases Vision Pro 2 with Enhanced Display",
            "link": "https://example.com/apple-vision-pro-2",
            "description": (
                "Apple today announced the Vision Pro 2, featuring a revolutionary 8K per eye "
                "display and improved battery life. The new headset weighs 30% less than its "
                "predecessor."
            ),
            "pubDate": "Mon, 26 May 2025 07:30:00 GMT",
        },
        {
            "title": "Major Cybersecurity Breach Affects Fortune 500 Companies",
            "link": "https://example.com/cyber-breach",
            "description": (
                "A sophisticated cyberattack has compromised data from over 50 Fortune 500 "
                "companies. Security experts are calling it one of the largest breaches in "
                "corporate history."
            ),
            "pubDate": "Mon, 26 May 2025 06:00:00 GMT",
        },
        {
            "title": "New Study Shows Benefits of 4-Day Work Week",
            "link": "https://example.com/4day-work-week",
            "description": (
                "A comprehensive study involving 100 companies shows that a 4-day work week "
                "increases productivity by 25% while improving employee satisfaction and mental "
                "health."
            ),
            "pubDate": "Sun, 25 May 2025 14:00:00 GMT",
        },
        {
            "title": "Tour de France 2025 Route Announced",
            "link": "https://example.com/tour-de-france",
            "description": (
                "The 2025 Tour de France route has been revealed, featuring challenging mountain "
                "stages in the Alps and Pyrenees. The race will cover 3,500 kilometers over 21 "
                "stages."
            ),
            "pubDate": "Sun, 25 May 2025 10:00:00 GMT",
        },
        {
            "title": "Breaking: AI System Solves Protein Folding Challenge",
            "link": "https://example.com/ai-protein-folding",
            "description": (
                "Researchers have developed an AI system that can accurately predict protein "
                "structures in minutes, potentially accelerating drug discovery and disease "
                "treatment research."
            ),
            "pubDate": "Mon, 26 May 2025 09:00:00 GMT",
        },
    ]


def test_rate_limiter():
    """Test the rate limiter functionality."""
    print("\n=== Testing Rate Limiter ===")

    # Set up test environment variables
    os.environ["CLAUDE_MAX_TOKENS"] = "50000"
    os.environ["CLAUDE_MAX_REQUESTS"] = "3"

    limiter = ClaudeRateLimiter()
    print(f"Initial state: {limiter.get_usage_stats()}")

    # Test token checking
    assert limiter.can_make_request(10000)
    print("✓ Can make request with 10,000 tokens")

    assert not limiter.can_make_request(60000)
    print("✓ Cannot make request with 60,000 tokens (exceeds limit)")

    # Test usage recording
    limiter.record_usage(15000)
    print(f"After first request: {limiter.get_usage_stats()}")

    limiter.record_usage(20000)
    print(f"After second request: {limiter.get_usage_stats()}")

    # Should still be able to make one more request
    assert limiter.can_make_request(10000)
    limiter.record_usage(10000)
    print(f"After third request: {limiter.get_usage_stats()}")

    # Should not be able to make more requests
    assert not limiter.can_make_request(1000)
    print("✓ Cannot make more requests after limit reached")


def test_token_estimation():
    """Test token estimation."""
    print("\n=== Testing Token Estimation ===")

    articles = create_sample_articles()
    estimated = estimate_tokens(articles)
    print(f"Estimated tokens for {len(articles)} articles: {estimated}")

    # Test with empty articles
    assert estimate_tokens([]) == 0
    print("✓ Empty articles return 0 tokens")

    # Test with single article
    single_article_tokens = estimate_tokens([articles[0]])
    print(f"Single article tokens: {single_article_tokens}")


def test_prompt_creation():
    """Test prompt creation."""
    print("\n=== Testing Prompt Creation ===")

    articles = create_sample_articles()[:2]  # Use just 2 articles for brevity
    prompt = create_categorization_prompt(articles)

    print("Generated prompt preview:")
    print(prompt[:500] + "...\n")

    # Verify prompt contains required elements
    assert "CATEGORIES" in prompt or "Categories" in prompt
    assert "Technology" in prompt
    assert "article_0" in prompt
    assert articles[0]["title"] in prompt
    print("✓ Prompt contains all required elements")


def test_api_key_retrieval():
    """Test API key retrieval (requires AWS credentials and parameter)."""
    print("\n=== Testing API Key Retrieval ===")

    # Set the parameter name and region
    os.environ["ANTHROPIC_API_KEY_PARAMETER"] = "rss-email-anthropic-api-key"

    # Set a default region if not already set
    if "AWS_DEFAULT_REGION" not in os.environ:
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    try:
        # This will only work if AWS credentials are configured and parameter exists
        retrieved_api_key = get_anthropic_api_key()
        print(f"✓ Successfully retrieved API key (length: {len(retrieved_api_key)})")
        return retrieved_api_key
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Catch all exceptions as this test is expected to fail in CI without AWS credentials
        print(f"⚠ Could not retrieve API key: {e}")
        print("  (This is expected if AWS credentials or parameter are not configured)")
        return None


def test_claude_processing(provided_api_key: Optional[str] = None):
    """Test the full Claude processing pipeline."""
    print("\n=== Testing Claude Processing ===")

    if not provided_api_key:
        print("⚠ Skipping Claude processing test (no API key available)")
        return None

    # Set up environment
    os.environ["ANTHROPIC_API_KEY_PARAMETER"] = "rss-email-anthropic-api-key"
    os.environ["CLAUDE_ENABLED"] = "true"
    os.environ["CLAUDE_MODEL"] = "claude-3-5-haiku-latest"
    os.environ["CLAUDE_MAX_TOKENS"] = "100000"
    os.environ["CLAUDE_MAX_REQUESTS"] = "5"

    # Create test articles
    articles = create_sample_articles()
    print(f"Processing {len(articles)} test articles...")

    # Process with Claude
    rate_limiter = ClaudeRateLimiter()
    result = process_articles_with_claude(articles, rate_limiter)

    if result:
        print("✓ Successfully processed articles with Claude")
        print("\nProcessing metadata:")
        print(json.dumps(result.processing_metadata, indent=2))

        print(f"\nCategories found: {list(result.categories.keys())}")

        # Show article distribution
        print("\nArticle distribution by category:")
        for category, articles_in_cat in result.categories.items():
            print(f"  {category}: {len(articles_in_cat)} articles")
            for article in articles_in_cat:
                print(f"    - {article.title[:50]}...")
                print(f"      Summary: {article.summary[:100]}...")

        # Test priority grouping
        print("\n=== Testing Priority Grouping ===")
        ordered = group_articles_by_priority(result)
        print("Categories in priority order:")
        for i, (category, _) in enumerate(ordered):
            print(f"  {i + 1}. {category}")

        return result

    print("✗ Failed to process articles with Claude")
    return None


def test_fallback_behavior():
    """Test fallback behavior when Claude is disabled or fails."""
    print("\n=== Testing Fallback Behavior ===")

    # Test with Claude disabled
    os.environ["CLAUDE_ENABLED"] = "false"
    articles = create_sample_articles()[:2]
    rate_limiter = ClaudeRateLimiter()

    result = process_articles_with_claude(articles, rate_limiter)
    assert result is None
    print("✓ Returns None when Claude is disabled")

    # Test with empty articles
    os.environ["CLAUDE_ENABLED"] = "true"
    result = process_articles_with_claude([], rate_limiter)
    assert result is None
    print("✓ Returns None for empty article list")


def test_json_compression():
    """Test JSON compression and decompression."""
    print("\n=== Testing JSON Compression ===")

    # Create test data
    test_data = {
        "categories": {
            "Technology": [
                {
                    "id": "article_0",
                    "title": "Test Article",
                    "link": "https://example.com",
                    "summary": "This is a test summary.",
                    "category": "Technology",
                    "pubdate": "Mon, 26 May 2025 12:00:00 GMT",
                    "related_articles": [],
                }
            ]
        }
    }

    # Compress
    compressed = compress_json(test_data)
    print(f"Original size: {len(json.dumps(test_data))} bytes")
    print(f"Compressed size: {len(compressed)} bytes")

    # Decompress
    decompressed = decompress_json(compressed)

    # Verify
    if decompressed == test_data:
        print("✓ Compression/decompression successful - data matches")
    else:
        print("✗ Compression test failed - data doesn't match")


def test_categorized_articles_integer_bug():
    """Test fix for the integer data bug in _create_categorized_articles."""
    print("\n" + "=" * 60)
    print("Testing _create_categorized_articles with problematic data structures")
    print("=" * 60)

    # Mock articles data
    mock_articles = [
        {
            "title": "Test Article 1",
            "description": "Description 1",
            "link": "https://example.com/1",
        },
        {
            "title": "Test Article 2",
            "description": "Description 2",
            "link": "https://example.com/2",
        },
    ]
    print(f"Created mock articles: {len(mock_articles)}")

    # Mock usage stats
    mock_usage_stats = {"processed_at": "2025-06-18T12:00:00", "tokens_used": 1000}

    # Test case 1: Integer instead of list
    print("\n=== Test Case 1: Integer instead of list ===")
    invalid_structure = {
        "categories": {
            "Technology": 42  # Integer instead of list of articles
        }
    }

    result = _create_categorized_articles(
        invalid_structure, mock_articles, mock_usage_stats
    )
    print(f"Result: {result}")
    print(f"Categories: {result.categories}")
    print(f"Number of categories: {len(result.categories)}")
    assert len(result.categories) == 0, (
        "Should have no categories when given an integer instead of list"
    )

    # Test case 2: Valid structure
    print("\n=== Test Case 2: Valid structure ===")
    valid_structure = {
        "categories": {
            "Technology": [
                {
                    "id": "article_0",
                    "title": "Test Article 1",
                    "link": "https://example.com/1",
                    "summary": "Summary 1",
                    "category": "Technology",
                    "pubdate": "Mon, 18 Jun 2025",
                    "related_articles": [],
                }
            ]
        }
    }

    result = _create_categorized_articles(
        valid_structure, mock_articles, mock_usage_stats
    )
    print(f"Result: {result}")
    print(f"Categories: {result.categories}")
    print(f"Number of categories: {len(result.categories)}")
    if "Technology" in result.categories:
        print(f"Number of technology articles: {len(result.categories['Technology'])}")
    assert "Technology" in result.categories, "Should have Technology category"
    assert len(result.categories["Technology"]) == 1, (
        "Should have one article in Technology category"
    )

    print("\nAll tests successful!")
    return True


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("RSS Email Article Processor Test Suite")
    print("=" * 60)

    # Run tests that don't require API access
    test_rate_limiter()
    test_token_estimation()
    test_prompt_creation()
    test_fallback_behavior()
    test_json_compression()
    test_categorized_articles_integer_bug()

    # Try to get API key for full integration test
    retrieved_key = test_api_key_retrieval()

    # Run Claude processing test if API key is available
    if retrieved_key or os.environ.get("ANTHROPIC_API_KEY"):
        test_claude_processing(retrieved_key)
    else:
        print("\n⚠ Skipping Claude integration test (no API key)")
        print("  To run full tests, ensure:")
        print("  1. AWS credentials are configured")
        print("  2. Parameter 'rss-email-anthropic-api-key' exists in Parameter Store")
        print("  3. Or set ANTHROPIC_API_KEY environment variable directly")

    print("\n" + "=" * 60)
    print("Test suite completed!")
    print("=" * 60)


if __name__ == "__main__":
    # Allow running specific tests via command line
    if len(sys.argv) > 1:
        test_name = sys.argv[1]
        if test_name == "rate_limiter":
            test_rate_limiter()
        elif test_name == "tokens":
            test_token_estimation()
        elif test_name == "prompt":
            test_prompt_creation()
        elif test_name == "api_key":
            test_api_key_retrieval()
        elif test_name == "claude":
            env_key = os.environ.get("ANTHROPIC_API_KEY") or test_api_key_retrieval()
            test_claude_processing(env_key)
        elif test_name == "fallback":
            test_fallback_behavior()
        elif test_name == "compression":
            test_json_compression()
        elif test_name == "int_bug":
            test_categorized_articles_integer_bug()
        else:
            print(f"Unknown test: {test_name}")
            print(
                "Available tests: rate_limiter, tokens, prompt, api_key, claude, fallback, compression, int_bug"
            )
    else:
        # Default to running the integer bug test if no arguments provided
        test_categorized_articles_integer_bug()
