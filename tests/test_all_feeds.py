#!/usr/bin/env python3
"""Test all feeds in feed_urls.json."""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

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
        logger.error(f"Import error: {e}")
        sys.exit(1)

# Get logger for this module
logger = logging.getLogger(__name__)


def load_feed_urls():
    """Load feed URLs from feed_urls.json."""
    feed_urls_path = Path(__file__).parent.parent / "feed_urls.json"
    with open(feed_urls_path, "r") as f:
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
    logger.info(f"Testing {len(feeds)} feeds")

    # Get a timestamp 3 days ago
    timestamp = datetime.now() - timedelta(days=3)

    results = {
        "success": [],
        "warning": [],
        "failed": [],
        "errors": {},  # Group by error type
    }

    for feed in feeds:
        try:
            name = feed["name"]
            url = feed["url"]
            logger.info(f"Testing feed: {name} ({url})")

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
                        f"✅ Success: {name} - received {len(feed_items):,} bytes of XML"
                    )
                    results["success"].append(feed_entry)
                else:
                    try:
                        # Try to see what we got (first 100 characters)
                        preview = feed_items[:100].decode("utf-8", errors="replace")
                        logger.warning(
                            f"⚠️ Warning: {name} - received {len(feed_items):,} bytes but doesn't look like XML. Preview: {preview}"
                        )

                        # Log more detailed information about first bytes as hex to help diagnose compression/encoding issues
                        hex_preview = " ".join(f"{b:02x}" for b in feed_items[:16])
                        logger.debug(f"First 16 bytes as hex for {name}: {hex_preview}")

                        # Try to detect common headers for compressed or binary formats
                        if len(feed_items) >= 2:
                            if feed_items[:2] == b"\x1f\x8b":
                                logger.debug(f"{name} has gzip magic number (1F 8B)")
                            elif feed_items[:2] == b"PK":
                                logger.debug(f"{name} might be a ZIP file (PK)")
                            elif (
                                feed_items[:4] == b"\xef\xbb\xbf<"
                                or feed_items[:3] == b"\xef\xbb\xbf"
                            ):
                                logger.debug(f"{name} has UTF-8 BOM")
                            elif (
                                feed_items[:2] == b"\xfe\xff"
                                or feed_items[:2] == b"\xff\xfe"
                            ):
                                logger.debug(f"{name} has UTF-16 BOM")
                            elif (
                                feed_items[:5] == b"<?xml"
                                or feed_items[:5] == b"<rss "
                                or feed_items[:6] == b"<feed>"
                            ):
                                logger.debug(
                                    f"{name} appears to start with XML but wasn't detected"
                                )
                    except Exception as decode_error:
                        logger.warning(
                            f"⚠️ Warning: {name} - received {len(feed_items):,} bytes but doesn't look like XML and couldn't decode preview: {decode_error}"
                        )
                    results["warning"].append(feed_entry)
            else:
                logger.error(f"❌ Failed: {name} - no content received")
                results["failed"].append(
                    {"name": name, "url": url, "error": "No content received"}
                )
        except Exception as e:
            error_str = str(e)
            logger.error(f"❌ Error: {name} - {error_str}")

            # Group by error type
            error_type = error_str.split(":", 1)[0] if ":" in error_str else error_str
            if error_type not in results["errors"]:
                results["errors"][error_type] = []

            results["errors"][error_type].append(
                {"name": name, "url": url, "error": error_str}
            )

            results["failed"].append({"name": name, "url": url, "error": error_str})

    # Print summary
    success_count = len(results["success"])
    warning_count = len(results["warning"])
    failed_count = len(results["failed"])
    total = success_count + warning_count + failed_count

    logger.info("\n" + "=" * 80)
    logger.info(
        f"SUMMARY: {success_count}/{total} feeds successfully retrieved ({success_count / total:.0%})"
    )
    logger.info(f"- ✅ Success: {success_count} feeds")
    logger.info(
        f"- ⚠️ Warning: {warning_count} feeds (content received but might not be XML)"
    )
    logger.info(f"- ❌ Failed: {failed_count} feeds")

    if warning_count > 0:
        logger.info("\nWARNING FEEDS (received data but might not be XML):")
        for feed in results["warning"]:
            logger.info(f"- {feed['name']} ({feed['bytes']:,} bytes)")

    if failed_count > 0:
        logger.info("\nFAILED FEEDS BY ERROR TYPE:")
        for error_type, feeds in results["errors"].items():
            logger.info(f"\n{error_type} ({len(feeds)} feeds):")
            for feed in feeds:
                logger.info(f"- {feed['name']}: {feed['url']}")

        # Provide suggestions for fixing common feed issues
        logger.info("\nSUGGESTIONS FOR FAILED FEEDS:")
        for error_type, feeds in results["errors"].items():
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

    return results


if __name__ == "__main__":
    logger.info("Starting test of all feeds...")
    try:
        results = test_all_feeds()
        sys.exit(0 if len(results["failed"]) == 0 else 1)
    except Exception as e:
        logger.error(f"Unhandled exception: {str(e)}", exc_info=True)
        sys.exit(1)
