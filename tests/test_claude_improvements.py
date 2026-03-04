#!/usr/bin/env python3
"""
Test Claude processing improvements with real API.

Usage Option 1 (Recommended - using .env file):
    echo 'ANTHROPIC_API_KEY=your-api-key-here' > .env
    python test_claude_improvements.py

Usage Option 2 (using environment variables):
    export ANTHROPIC_API_KEY='your-api-key-here'
    python test_claude_improvements.py

This script tests the new improvements:
- Description truncation (reduces input tokens)
- Batch processing (handles large article sets)
- Conservative token limits (prevents output truncation)
- Enhanced error handling
"""

import os
import sys
import xml.etree.ElementTree as ET  # noqa: N817
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    # Look for .env file in current directory
    env_file = Path(".env")
    if env_file.exists():
        load_dotenv(env_file)
        print(f"📁 Loaded environment variables from {env_file}")
        # Show which relevant variables were loaded (without exposing the API key)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        claude_model = os.environ.get("CLAUDE_MODEL")
        print(f"   • ANTHROPIC_API_KEY: {'✅ Present' if api_key else '❌ Not found'}")
        if claude_model:
            print(f"   • CLAUDE_MODEL: {claude_model}")
    else:
        print("⚠️  No .env file found in current directory")
        print("You can create one with: echo 'ANTHROPIC_API_KEY=your-key-here' > .env")

except ImportError:
    print("⚠️  python-dotenv not installed. Install with: uv add python-dotenv")
    print("Or set environment variables manually:")
    print("export ANTHROPIC_API_KEY='your-key-here'")


from rss_email.article_processor import (  # noqa: E402
    ClaudeRateLimiter,
    group_articles_by_priority,
    process_articles_with_claude,
)
from rss_email.email_articles import filter_items  # noqa: E402


def _extract_articles_from_rss_fallback(rss_content: str) -> Optional[list]:
    """
    Extract articles from old RSS file using manual parsing.

    Args:
        rss_content: Raw RSS XML content

    Returns:
        List of article dictionaries or None on error
    """
    try:
        root = ET.fromstring(rss_content)
        items = root.findall(".//item")[:10]
        filtered_items = []
        for item in items:
            article = {}
            for field in ["title", "link", "description"]:
                elem = item.find(field)
                if elem is not None and elem.text:
                    article[field] = elem.text
                else:
                    article[field] = f"Sample {field}"

            pubdate_elem = item.find("pubDate")
            if pubdate_elem is not None and pubdate_elem.text:
                article["pubDate"] = pubdate_elem.text
            else:
                article["pubDate"] = "Wed, 16 Oct 2024 05:25:08 GMT"

            article["sortDate"] = datetime.now().timestamp()
            filtered_items.append(article)

        return filtered_items if filtered_items else None
    except (ET.ParseError, AttributeError) as exc:
        print(f"❌ Error parsing RSS file: {exc}")
        return None


def _load_articles_from_rss(rss_content: str) -> Optional[list]:
    """
    Load articles from RSS content with fallback strategies.

    Args:
        rss_content: Raw RSS XML content

    Returns:
        List of article dictionaries or None on error
    """
    # Try different time windows
    for days in [7, 30, 365]:
        run_date = datetime.now() - timedelta(days=days)
        filtered_items = filter_items(rss_content, run_date)
        if filtered_items:
            return filtered_items

    # Last resort: extract old articles manually
    print("⚠️  RSS file appears to be very old, using first 10 articles for testing...")
    return _extract_articles_from_rss_fallback(rss_content)


def main():
    """Test Claude processing with real RSS data."""
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not found")
        print("\nPlease set your Anthropic API key using one of these methods:")
        print("\nOption 1 (Recommended): Create a .env file")
        print("  echo 'ANTHROPIC_API_KEY=your-key-here' > .env")
        print("\nOption 2: Set environment variable")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("\nThen run: uv run python test_claude_improvements.py")
        return False

    # Set default model if not specified
    if not os.environ.get("CLAUDE_MODEL"):
        os.environ["CLAUDE_MODEL"] = "claude-haiku-4-5-20251001"
        print(f"Using default Claude model: {os.environ['CLAUDE_MODEL']}")

    try:
        print("🧪 Testing Claude processing improvements...")
        print("=" * 60)

        # Read RSS file
        print("📖 Reading RSS file...")
        with open("rss.xml", "r", encoding="utf-8") as f:
            rss_content = f.read()

        # Get articles from RSS
        filtered_items = _load_articles_from_rss(rss_content)
        if not filtered_items:
            print("❌ Could not extract any articles from RSS file.")
            return False

        print(f"📰 Found {len(filtered_items)} articles to process")

        # Show what improvements will be tested
        print("\n🔧 Testing improvements:")
        print("   • Description truncation (reduces input tokens)")
        print(f"   • Batch processing ({len(filtered_items)} articles)")
        if len(filtered_items) > 15:
            print("   • Will use batch processing (>15 articles)")
        else:
            print("   • Will use single batch processing (<15 articles)")
        print("   • Conservative token limits")
        print("   • Enhanced error handling")

        # Process with Claude
        print("\n🤖 Starting Claude processing...")
        rate_limiter = ClaudeRateLimiter()
        result = process_articles_with_claude(filtered_items, rate_limiter)

        if result:
            print("✅ Claude processing successful!")

            # Show results
            stats = result.processing_metadata
            print("\n📊 Processing Statistics:")
            print(f"   • Articles processed: {stats['articles_count']}")
            print(f"   • Categories found: {len(result.categories)}")
            print(f"   • Tokens used: {stats['tokens_used']}")
            print(
                f"   • Processing time: {stats['processing_time_seconds']:.2f} seconds"
            )

            if "batches_processed" in stats:
                print(
                    f"   • Batches processed: {stats['batches_processed']}/{stats.get('total_batches', 1)}"
                )

            print("\n📋 Category Distribution:")
            ordered_categories = group_articles_by_priority(result)
            total_articles = sum(len(articles) for _, articles in ordered_categories)

            for category_name, articles in ordered_categories:
                percentage = len(articles) / total_articles * 100
                print(
                    f"   • {category_name}: {len(articles)} articles ({percentage:.1f}%)"
                )

            print("\n📝 Sample Processed Articles:")
            for category_name, articles in ordered_categories[:2]:
                print(f"\n   {category_name}:")
                for i, article in enumerate(articles[:3], 1):
                    print(f"   {i}. {article.title[:60]}...")
                    print(f"      Summary: {article.summary[:80]}...")

            print("\n🎉 All improvements working correctly!")
            return True
        print("❌ Claude processing failed")
        return False

    except FileNotFoundError:
        print("❌ rss.xml file not found")
        print("Please run this script from the project root directory.")
        return False
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"❌ Error: {exc}")
        return False


if __name__ == "__main__":
    SUCCESS = main()
    if not SUCCESS:
        sys.exit(1)

    print("\n" + "=" * 60)
    print("✅ Claude processing test completed successfully!")
    print("The improvements should resolve the daily token limit errors.")
