#!/usr/bin/env python3
"""Test decompression of problematic feeds."""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

try:
    from rss_email.retrieve_articles import detect_and_decompress, get_feed_items
except ImportError:
    # Add src to path for direct imports
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from rss_email.retrieve_articles import detect_and_decompress, get_feed_items

# List of problematic feeds that need special handling
PROBLEM_FEEDS = [
    {
        "name": "Enterprise – TechCrunch",
        "url": "https://feeds.feedburner.com/techcrunchIt",
    },
    {"name": "Facebook Engineering", "url": "https://engineering.fb.com/feed/"},
    {"name": "xkcd.com", "url": "https://xkcd.com/rss.xml"},
    {
        "name": "Top News - MIT Technology Review",
        "url": "https://www.technologyreview.com/topnews.rss?from=feedstr",
    },
    {
        "name": "Highly Scalable Blog",
        "url": "https://highlyscalable.wordpress.com/feed/",
    },
    {
        "name": "The Verge - Top Stories",
        "url": "https://www.theverge.com/rss/index.xml",
    },
    {"name": "Martin Heinz", "url": "https://martinheinz.dev/rss"},
    {"name": "GitHub Blog", "url": "https://github.blog/feed/"},
]


def test_problematic_feeds():
    """Test decompression for all problematic feeds."""
    logger.info(f"Testing {len(PROBLEM_FEEDS)} problematic feeds...")

    success_count = 0
    failed_feeds = []

    # Get a timestamp 3 days ago for conditional requests
    timestamp = datetime.now() - timedelta(days=3)

    for feed in PROBLEM_FEEDS:
        feed_url = feed["url"]
        feed_name = feed["name"]

        logger.info(f"Testing feed: {feed_name} ({feed_url})")

        try:
            # Fetch the feed
            feed_content = get_feed_items(feed_url, timestamp)

            if not feed_content:
                logger.error(f"❌ No content received for {feed_name}")
                failed_feeds.append(feed_name)
                continue

            # Check if it looks like XML
            if (
                b"<rss" in feed_content[:500]
                or b"<feed" in feed_content[:500]
                or b"<?xml" in feed_content[:500]
                or b"<xml" in feed_content[:500]
            ):
                logger.info(
                    f"✅ Success: {feed_name} - received {len(feed_content):,} bytes of valid XML"
                )
                success_count += 1
            else:
                logger.warning(
                    f"⚠️ Warning: {feed_name} - received {len(feed_content):,} bytes but doesn't look like XML"
                )

                # Try our specialized decompression function again
                decompressed = detect_and_decompress(feed_content, feed_url)

                if (
                    b"<rss" in decompressed[:500]
                    or b"<feed" in decompressed[:500]
                    or b"<?xml" in decompressed[:500]
                    or b"<xml" in decompressed[:500]
                ):
                    logger.info(f"✅ Success after extra decompression: {feed_name}")
                    success_count += 1
                else:
                    failed_feeds.append(feed_name)

                    # Show content preview for debugging
                    try:
                        preview = feed_content[:100].decode("utf-8", errors="replace")
                        logger.debug(f"Content preview: {preview}")
                    except Exception:
                        pass

                    # Show hex dump of first bytes
                    hex_preview = " ".join(f"{b:02x}" for b in feed_content[:32])
                    logger.debug(f"First 32 bytes as hex: {hex_preview}")
        except Exception as e:
            logger.error(f"❌ Error testing {feed_name}: {str(e)}")
            failed_feeds.append(feed_name)

    logger.info(
        f"\nRESULTS: {success_count}/{len(PROBLEM_FEEDS)} feeds successfully retrieved ({success_count / len(PROBLEM_FEEDS):.0%})"
    )

    if failed_feeds:
        logger.info("Failed feeds: " + ", ".join(failed_feeds))

    # For pytest, we'll allow the test to pass if we have at least 6 out of 8 feeds working
    # The TechCrunch feed is special-cased to use a direct feed URL instead
    min_success = len(PROBLEM_FEEDS) - 2  # Allow up to 2 feeds to fail
    assert success_count >= min_success, (
        f"{len(failed_feeds)} feeds failed: {', '.join(failed_feeds)}"
    )


def main():
    """Run tests for all problematic feeds when script is run directly."""
    try:
        test_problematic_feeds()
        return 0
    except AssertionError as e:
        logger.error(f"Test failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
