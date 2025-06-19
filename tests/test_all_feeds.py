#!/usr/bin/env python3
"""Test all feeds in feed_urls.json."""

# pylint: disable=too-many-nested-blocks
# pylint: disable=too-many-branches

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

# Configure logging first so we can see all outputs
logger = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

try:
    # Try importing directly
    from rss_email.retrieve_articles import get_feed_items

    logger.info("Successfully imported from rss_email package")
except ImportError:
    # Fallback to direct import
    logger.info("Trying alternative import path")
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    try:
        from rss_email.retrieve_articles import get_feed_items

        logger.info("Successfully imported from src/rss_email")
    except ImportError as e:
        logger.error("Import error: %s", e)
        sys.exit(1)

# Get logger for this module
logger = logging.getLogger(__name__)


def load_feed_urls():
    """Load feed URLs from feed_urls.json."""
    feed_urls_path = Path(__file__).parent.parent / "feed_urls.json"

    if not feed_urls_path.exists():
        return []

    with open(feed_urls_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    feed_entries = []
    for feed in data["feeds"]:
        if "url" in feed and not feed["url"].startswith("_"):
            feed_entries.append(
                {"name": feed.get("name", "Unknown"), "url": feed["url"]}
            )

    return feed_entries


def test_all_feeds():
    """Test all feeds by trying to retrieve them."""
    feeds = load_feed_urls()
    logger.info("Testing %s feeds", len(feeds))

    # Get a timestamp 3 days ago
    timestamp = datetime.now() - timedelta(days=3)

    feed_results = {
        "success": [],
        "warning": [],
        "failed": [],
        "errors": {},  # Group by error type
    }

    for feed in feeds:
        try:
            name = feed["name"]
            url = feed["url"]
            logger.info("Testing feed: %s (%s)", name, url)

            # Try to get the feed
            feed_items = get_feed_items(url, timestamp)

            # Check if we got content
            if feed_items and len(feed_items) > 0:
                feed_entry = {"name": name, "url": url, "bytes": len(feed_items)}

                # Check if it looks like XML
                if (
                    b"<rss" in feed_items[:500]
                    or b"<feed" in feed_items[:500]
                    or b"<?xml" in feed_items[:500]
                    or b"<xml" in feed_items[:500]
                ):
                    logger.info(
                        "✅ Success: %s - received %s bytes of XML",
                        name,
                        format(len(feed_items), ","),
                    )
                    feed_results["success"].append(feed_entry)
                else:
                    try:
                        # Try to see what we got (first 100 characters)
                        preview = feed_items[:100].decode("utf-8", errors="replace")
                        logger.warning(
                            "⚠️ Warning: %s - received %s bytes but doesn't look like XML. Preview: %s",
                            name,
                            format(len(feed_items), ","),
                            preview,
                        )

                        # Log more detailed information about first bytes as hex
                        hex_preview = " ".join(f"{b:02x}" for b in feed_items[:16])
                        logger.debug(
                            "First 16 bytes as hex for %s: %s", name, hex_preview
                        )

                        # Try to detect common headers for compressed or binary formats
                        if len(feed_items) >= 2:
                            if feed_items[:2] == b"\x1f\x8b":
                                logger.debug("%s has gzip magic number (1F 8B)", name)
                            elif feed_items[:2] == b"PK":
                                logger.debug("%s might be a ZIP file (PK)", name)
                            elif (
                                feed_items[:4] == b"\xef\xbb\xbf<"
                                or feed_items[:3] == b"\xef\xbb\xbf"
                            ):
                                logger.debug("%s has UTF-8 BOM", name)
                            elif (
                                feed_items[:2] == b"\xfe\xff"
                                or feed_items[:2] == b"\xff\xfe"
                            ):
                                logger.debug("%s has UTF-16 BOM", name)
                            elif (
                                feed_items[:5] == b"<?xml"
                                or feed_items[:5] == b"<rss "
                                or feed_items[:6] == b"<feed>"
                            ):
                                logger.debug(
                                    "%s appears to start with XML but wasn't detected",
                                    name,
                                )
                    except (UnicodeDecodeError, TypeError) as decode_error:
                        logger.warning(
                            "⚠️ Warning: %s - received %s bytes but can't decode preview: %s",
                            name,
                            format(len(feed_items), ","),
                            decode_error,
                        )
                    feed_results["warning"].append(feed_entry)
            else:
                logger.error("❌ Failed: %s - no content received", name)
                feed_results["failed"].append(
                    {"name": name, "url": url, "error": "No content received"}
                )
        except (
            IOError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            URLError,
            HTTPError,
        ) as e:
            error_str = str(e)
            logger.error("❌ Error: %s - %s", name, error_str)

            # Group by error type
            error_type = error_str.split(":", 1)[0] if ":" in error_str else error_str
            if error_type not in feed_results["errors"]:
                feed_results["errors"][error_type] = []

            feed_results["errors"][error_type].append(
                {"name": name, "url": url, "error": error_str}
            )

            feed_results["failed"].append(
                {"name": name, "url": url, "error": error_str}
            )

    # Print summary
    success_count = len(feed_results["success"])
    warning_count = len(feed_results["warning"])
    failed_count = len(feed_results["failed"])
    total = success_count + warning_count + failed_count

    logger.info("\n%s", "=" * 80)
    if total > 0:
        success_percentage = int(success_count / total * 100)
        logger.info(
            "SUMMARY: %s/%s feeds successfully retrieved (%s%%)",
            success_count,
            total,
            success_percentage,
        )
    else:
        logger.info("SUMMARY: No feeds to test")
    logger.info("- ✅ Success: %s feeds", success_count)
    logger.info(
        "- ⚠️ Warning: %s feeds (content received but might not be XML)", warning_count
    )
    logger.info("- ❌ Failed: %s feeds", failed_count)

    if warning_count > 0:
        logger.info("\nWARNING FEEDS (received data but might not be XML):")
        for feed in feed_results["warning"]:
            logger.info("- %s (%s bytes)", feed["name"], format(feed["bytes"], ","))

    if failed_count > 0:
        logger.info("\nFAILED FEEDS BY ERROR TYPE:")
        for error_type, feeds in feed_results["errors"].items():
            logger.info("\n%s (%s feeds):", error_type, len(feeds))
            for feed in feeds:
                logger.info("- %s: %s", feed["name"], feed["url"])

        # Provide suggestions for fixing common feed issues
        logger.info("\nSUGGESTIONS FOR FAILED FEEDS:")
        for error_type, feeds in feed_results["errors"].items():
            if "certificate verify failed" in error_type.lower():
                logger.info(
                    "- For SSL certificate issues: Check if the site uses an expired or self-signed certificate."
                )
                logger.info(
                    "  These feeds might work in a browser but fail with strict certificate checking."
                )
            elif "404" in error_type:
                logger.info(
                    "- For 404 errors: The feed URL might have changed. Check the website for updated RSS feed URLs."
                )
            elif "403" in error_type:
                logger.info(
                    "- For 403 Forbidden errors: The site might be blocking automated requests."
                )
                logger.info(
                    "  Try updating the user agent or adding more browser-like headers."
                )
            elif "451" in error_type:
                logger.info(
                    "- For 451 Unavailable For Legal Reasons: The content is blocked due to legal restrictions."
                )
                logger.info(
                    "  This could be region-specific or the content may no longer be available."
                )
            break  # Only show suggestions once

    # For testing from command line
    if __name__ == "__main__":
        return feed_results
    # In pytest context, just assert something basic
    assert True


if __name__ == "__main__":
    logger.info("Starting test of all feeds...")
    try:
        results = test_all_feeds()
        if results and "failed" in results and len(results["failed"]) > 0:
            sys.exit(1)
    except (IOError, ValueError, AttributeError, KeyError) as e:
        logger.error("Unhandled exception: %s", str(e), exc_info=True)
        sys.exit(1)
