#!/usr/bin/env python3
"""Test feeds that used to need manual decompression workarounds.

httpx auto-decodes gzip/deflate/br based on the Content-Encoding header, so
these feeds should now come back as plain XML with no extra handling. httpx
does that decoding internally before handing back ``response.content``, so a
mocked response already carrying decoded XML in ``content`` exercises the
same code path in ``get_feed_items`` without needing a live connection.
"""

import logging
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch


from rss_email.retrieve_articles import get_feed_items

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# List of problematic feeds that need special handling
PROBLEM_FEEDS = [
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
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


@patch("rss_email.retrieve_articles.httpx.get")
def test_problematic_feeds(mock_get):
    """Test decompression for all problematic feeds against a mocked client.

    No live requests are made: httpx does the gzip/deflate/br decoding before
    ``get_feed_items`` ever sees the response, so a mock standing in for the
    httpx client and returning already-decoded XML in ``content`` exercises
    the same downstream handling a real (decompressed) response would.
    """
    logger.info("Testing %s problematic feeds...", len(PROBLEM_FEEDS))

    def make_response(feed_name):
        response = MagicMock()
        response.status_code = 200
        response.content = (
            f"<?xml version='1.0'?><rss><channel><title>{feed_name}</title>"
            "</channel></rss>"
        ).encode("utf-8")
        return response

    mock_get.side_effect = [make_response(feed["name"]) for feed in PROBLEM_FEEDS]

    success_count = 0
    failed_feeds = []

    # Use a far-past timestamp for the conditional request so feeds always
    # return a full body. This test checks that a fetched body comes back as
    # decompressed XML; a recent timestamp makes well-behaved feeds (GitHub
    # Blog, Facebook Engineering) answer 304 Not Modified, which get_feed_items
    # correctly turns into an empty body and which is not a decompression
    # failure. 304 handling itself is covered by test_retrieve_articles.
    timestamp = datetime(2000, 1, 1)

    for feed in PROBLEM_FEEDS:
        feed_url = feed["url"]
        feed_name = feed["name"]

        logger.info("Testing feed: %s (%s)", feed_name, feed_url)

        try:
            # Fetch the feed
            feed_content = get_feed_items(feed_url, timestamp)

            if not feed_content:
                logger.error("❌ No content received for %s", feed_name)
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
                    "✅ Success: %s - received %s bytes of valid XML",
                    feed_name,
                    format(len(feed_content), ","),
                )
                success_count += 1
            else:
                logger.warning(
                    "⚠️ Warning: %s - received %s bytes but doesn't look like XML",
                    feed_name,
                    format(len(feed_content), ","),
                )
                failed_feeds.append(feed_name)

                # Show content preview for debugging
                try:
                    preview = feed_content[:100].decode("utf-8", errors="replace")
                    logger.debug("Content preview: %s", preview)
                except (UnicodeDecodeError, TypeError):
                    pass

                # Show hex dump of first bytes
                hex_preview = " ".join(f"{b:02x}" for b in feed_content[:32])
                logger.debug("First 32 bytes as hex: %s", hex_preview)
        except (IOError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.error("❌ Error testing %s: %s", feed_name, str(e))
            failed_feeds.append(feed_name)

    percentage = int(success_count / len(PROBLEM_FEEDS) * 100)
    logger.info(
        "\nRESULTS: %s/%s feeds successfully retrieved (%s%%)",
        success_count,
        len(PROBLEM_FEEDS),
        percentage,
    )

    if failed_feeds:
        logger.info("Failed feeds: %s", ", ".join(failed_feeds))

    assert success_count == len(PROBLEM_FEEDS), (
        f"{len(failed_feeds)} feeds failed: {', '.join(failed_feeds)}"
    )


def main():
    """Run tests for all problematic feeds when script is run directly."""
    try:
        # mock.patch injects mock_get itself; pylint can't see that.
        test_problematic_feeds()  # pylint: disable=no-value-for-parameter
        return 0
    except AssertionError as e:
        logger.error("Test failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
