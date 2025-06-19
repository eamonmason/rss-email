#!/usr/bin/env python3
"""Lambda function to aggregate multiple RSS feeds into a single one."""

# pylint: disable=broad-exception-caught
# pylint: disable=too-many-nested-blocks
# pylint: disable=too-many-return-statements
# pylint: disable=too-many-branches
# pylint: disable=too-many-statements
# pylint: disable=protected-access

# pylint: disable=broad-exception-caught

from __future__ import annotations, print_function

import argparse
import concurrent.futures
import contextlib
import gzip
import io
import json
import logging
import os
import re
import socket
import ssl
import sys
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from importlib.resources import files
from socket import timeout
from time import mktime
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError

import boto3
import feedparser
import pydantic
import PyRSS2Gen
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, HttpUrl

# Import brotli if available
try:
    import brotli
except ImportError:
    brotli = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CHARACTER_ENCODING = "utf-8"
REMOTE_SERVER = "www.google.com"
DAYS_OF_NEWS = 3


@pydantic.validate_call(validate_return=True)
def get_feed_items(url: str, timestamp: datetime) -> bytes:
    """Slurps feed url."""

    feed_items = b""
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )

    # More comprehensive headers to mimic a real browser
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        # Some sites check Referer to avoid scraping
        "Referer": "https://www.google.com/",
    }

    # Add If-Modified-Since header for conditional request
    check_headers = headers.copy()
    check_headers["If-Modified-Since"] = timestamp.strftime("%a, %d %b %Y %H:%M:%S GMT")

    # We'll create an unverified SSL context when needed in the exception handlers
    # This is generally not recommended for production, but we're handling feeds
    # from many sources, some of which may have certificate issues

    req_check_new = urllib.request.Request(
        url,
        data=None,
        headers=check_headers,
    )

    req_retrieve = urllib.request.Request(url, data=None, headers=headers)

    try:
        # Try to handle common edge cases for HTTP(S) request problems

        # First attempt - use the standard approach with SSL verification
        if url.startswith("https"):
            try:
                # First try with SSL verification
                with contextlib.closing(
                    urllib.request.urlopen(req_check_new, timeout=10)
                ):
                    with urllib.request.urlopen(req_retrieve, timeout=10) as conn:
                        content = conn.read()
                        feed_items = detect_and_decompress(content, url)
            except (ssl.SSLError, URLError, HTTPError) as ssl_error:
                logger.debug(
                    "SSL verification failed for %s: %s. Trying without verification.",
                    url,
                    str(ssl_error),
                )
                # If SSL verification fails, try with an unverified context
                try:
                    unverified_context = ssl._create_unverified_context()
                    with contextlib.closing(
                        urllib.request.urlopen(
                            req_check_new, timeout=10, context=unverified_context
                        )
                    ):
                        with urllib.request.urlopen(
                            req_retrieve, timeout=10, context=unverified_context
                        ) as conn:
                            content = conn.read()
                            feed_items = detect_and_decompress(content, url)
                except (
                    ssl.SSLError,
                    ConnectionError,
                    TimeoutError,
                    OSError,
                    socket.timeout,
                    URLError,
                    HTTPError,
                ) as e:
                    logger.debug(
                        "Unverified SSL attempt failed for %s: %s. Trying opener approach.",
                        url,
                        str(e),
                    )
                    # If that fails too, try with a custom opener
                    try:
                        opener = urllib.request.build_opener(
                            urllib.request.HTTPSHandler(context=unverified_context)
                        )
                        opener.addheaders = list(headers.items())
                        with contextlib.closing(opener.open(url, timeout=10)) as conn:
                            content = conn.read()
                            feed_items = detect_and_decompress(content, url)
                    except (
                        ssl.SSLError,
                        ConnectionError,
                        TimeoutError,
                        OSError,
                        timeout,
                        URLError,
                        HTTPError,
                    ) as e3:
                        logger.debug(
                            "All SSL approaches failed for %s: %s", url, str(e3)
                        )
                        raise
            except (ConnectionError, TimeoutError, OSError) as e:
                logger.debug(
                    "First attempt failed for %s: %s. Trying alternate approach.",
                    url,
                    str(e),
                )
                # Try a different approach for some sites (non-SSL related issues)
                try:
                    opener = urllib.request.build_opener()
                    opener.addheaders = list(headers.items())
                    with contextlib.closing(opener.open(url, timeout=10)) as conn:
                        content = conn.read()
                        feed_items = detect_and_decompress(content, url)
                except Exception as e2:
                    logger.debug("Alternative approach failed for %s: %s", url, str(e2))
                    raise
        else:
            # Non-HTTPS case
            with contextlib.closing(urllib.request.urlopen(req_check_new, timeout=10)):
                with urllib.request.urlopen(req_retrieve, timeout=10) as conn:
                    content = conn.read()
                    feed_items = detect_and_decompress(content, url)

        # As a final fallback for binary/compressed content, try to detect if it's XML
        if feed_items and not (
            b"<rss" in feed_items or b"<feed" in feed_items or b"<?xml" in feed_items
        ):
            logger.debug(
                "Content doesn't look like XML, attempting decompression for: %s", url
            )
            feed_items = detect_and_decompress(feed_items, url)

    except HTTPError as error:
        if error.code == 304:
            logger.debug("URL: %s not modified in 3 days", url)
        elif error.code == 403:
            logger.error(
                "URL: %s returned 403 Forbidden. This might be a site that aggressively blocks scrapers.",
                url,
            )
        else:
            logger.error("URL: %s, data not retrieved because %s", url, error)
    except URLError as error:
        error_str = str(error)
        logger.error("URL: %s, url error %s", url, error)

        # For HTTP protocol errors, try converting HTTPS to HTTP as a last resort
        if "ssl" in error_str.lower() and url.startswith("https://"):
            try:
                logger.debug("Trying HTTP fallback for %s", url)
                http_url = url.replace("https://", "http://")
                http_req = urllib.request.Request(http_url, data=None, headers=headers)
                with contextlib.closing(
                    urllib.request.urlopen(http_req, timeout=10)
                ) as conn:
                    content = conn.read()
                    if content:
                        feed_items = detect_and_decompress(content, url)
                        logger.info(
                            "Successfully retrieved feed using HTTP fallback: %s", url
                        )
            except Exception as http_error:
                logger.debug(
                    "HTTP fallback also failed for %s: %s", url, str(http_error)
                )
    except timeout:
        logger.error("socket timed out - URL %s", url)
    except Exception as general_error:
        logger.error("Unexpected error retrieving %s: %s", url, str(general_error))
    else:
        if not feed_items:
            logger.debug("URL: %s - no feed items", url)
    return feed_items


def detect_and_decompress(content: bytes, url: str) -> bytes:
    """
    Detect and decompress content based on signature or file format.
    Supports various compression formats including gzip, deflate, brotli, and content with specific signatures.

    Args:
        content: The compressed or encoded content
        url: The URL of the feed (for logging purposes)

    Returns:
        Decompressed content or original content if decompression fails
    """
    if not content or len(content) < 4:
        return content

    logger.debug("Attempting signature-based decompression for %s", url)

    # Check for specific site patterns first
    if url == "https://github.blog/feed/" and content[:8].startswith(b"U\xaaD"):
        # GitHub blog specific handling
        try:
            decompressed = zlib.decompress(content)
            logger.debug("GitHub blog decompression successful")
            return decompressed
        except Exception:
            try:
                # Try with window bits setting
                decompressed = zlib.decompress(content, 31)
                logger.debug("GitHub blog decompression successful with window bits 31")
                return decompressed
            except Exception:
                pass

    elif url == "https://www.theverge.com/rss/index.xml" and content[:8].startswith(
        b"U\xaa"
    ):
        # The Verge specific handling
        try:
            decompressed = zlib.decompress(content)
            logger.debug("The Verge decompression successful")
            return decompressed
        except Exception:
            try:
                # Try with window bits setting
                decompressed = zlib.decompress(content, 31)
                logger.debug("The Verge decompression successful with window bits 31")
                return decompressed
            except Exception:
                pass

    elif url == "https://martinheinz.dev/rss" and content[:4].startswith(b"\xfa:A"):
        # Martin Heinz specific handling - likely compressed with deflate
        try:
            for window_bits in [15, 31, -15]:
                try:
                    decompressed = zlib.decompress(content, window_bits)
                    if (
                        b"<rss" in decompressed[:500]
                        or b"<feed" in decompressed[:500]
                        or b"<?xml" in decompressed[:500]
                    ):
                        logger.debug(
                            "Martin Heinz decompression successful with window bits %d",
                            window_bits,
                        )
                        return decompressed
                except Exception:
                    pass
        except Exception:
            pass

    elif url == "https://xkcd.com/rss.xml" and len(content) > 10:
        # XKCD specific handling
        try:
            # Try with different offsets to find the gzip header
            for i in range(10):
                try:
                    buffer = io.BytesIO(content[i:])
                    with gzip.GzipFile(fileobj=buffer) as gzipped:
                        decompressed = gzipped.read()
                        if (
                            b"<rss" in decompressed[:500]
                            or b"<feed" in decompressed[:500]
                            or b"<?xml" in decompressed[:500]
                        ):
                            logger.debug(
                                "XKCD decompression successful with %d byte offset", i
                            )
                            return decompressed
                except Exception:
                    pass
        except Exception:
            pass

    elif url == "https://feeds.feedburner.com/techcrunchIt":
        # FeedBurner often redirects to a landing page
        # We'll use a direct TechCrunch feed URL instead
        logger.info("Redirecting TechCrunch feed to direct URL")
        direct_url = "https://techcrunch.com/feed/"
        timestamp = datetime.now() - timedelta(days=3)
        return get_feed_items(direct_url, timestamp)

    # General detection and decompression based on signatures

    # Check for gzip signature (1F 8B)
    if content.startswith(b"\x1f\x8b"):
        try:
            buffer = io.BytesIO(content)
            with gzip.GzipFile(fileobj=buffer) as gzipped:
                decompressed = gzipped.read()
                logger.debug("Standard gzip decompression successful")
                return decompressed
        except Exception as e:
            logger.debug("Gzip decompression failed: %s", str(e))

    # Check for zlib signature (78 01, 78 9C, 78 DA)
    if len(content) > 2 and content[0] == 0x78 and content[1] in (0x01, 0x9C, 0xDA):
        try:
            decompressed = zlib.decompress(content)
            logger.debug("Zlib decompression successful")
            return decompressed
        except Exception as e:
            logger.debug("Zlib decompression failed: %s", str(e))

    # Check for Facebook Engineering compressed feed (known pattern)
    if url == "https://engineering.fb.com/feed/" and content.startswith(b"UT"):
        try:
            # Try different decompressions
            for window_bits in [47, 31, 15, -15]:
                try:
                    decompressed = zlib.decompress(content, window_bits)
                    if (
                        b"<rss" in decompressed[:500]
                        or b"<feed" in decompressed[:500]
                        or b"<?xml" in decompressed[:500]
                    ):
                        logger.debug(
                            "Facebook Engineering decompression successful with window bits %d",
                            window_bits,
                        )
                        return decompressed
                except Exception:
                    pass

            # If that fails, try decompressing with various offsets
            for i in range(4):
                try:
                    decompressed = zlib.decompress(content[i:], 31)
                    if (
                        b"<rss" in decompressed[:500]
                        or b"<feed" in decompressed[:500]
                        or b"<?xml" in decompressed[:500]
                    ):
                        logger.debug(
                            "Facebook Engineering decompression successful with %d byte offset",
                            i,
                        )
                        return decompressed
                except Exception:
                    pass
        except Exception:
            pass

    # Try to detect MS Compression (Technology Review and others)
    if content.startswith(b"Un") or content.startswith(b"U\xaa"):
        try:
            # Try different zlib window bits
            for window_bits in [47, 31, 15, -15]:
                try:
                    decompressed = zlib.decompress(content, window_bits)
                    if (
                        b"<rss" in decompressed[:500]
                        or b"<feed" in decompressed[:500]
                        or b"<?xml" in decompressed[:500]
                    ):
                        logger.debug(
                            "MS decompression successful with window bits %d",
                            window_bits,
                        )
                        return decompressed
                except Exception:
                    pass
        except Exception:
            pass

    # Try brotli if available
    if brotli is not None:
        try:
            decompressed = brotli.decompress(content)
            if (
                b"<rss" in decompressed[:500]
                or b"<feed" in decompressed[:500]
                or b"<?xml" in decompressed[:500]
            ):
                logger.debug("Brotli decompression successful")
                return decompressed
        except Exception:
            pass

    # Try to brute force with common compression algorithms
    # Try zlib/deflate with different window bits
    for window_bits in [47, 31, 15, -15, -8]:
        try:
            decompressed = zlib.decompress(content, window_bits)
            if (
                b"<rss" in decompressed[:500]
                or b"<feed" in decompressed[:500]
                or b"<?xml" in decompressed[:500]
            ):
                logger.debug(
                    "Zlib decompression successful with window bits %d", window_bits
                )
                return decompressed
        except Exception:
            pass

    # Try different starting offsets
    for i in range(10):
        if len(content) <= i:
            break
        # Try gzip with offset
        try:
            if len(content) > i + 2 and content[i : i + 2] == b"\x1f\x8b":
                buffer = io.BytesIO(content[i:])
                with gzip.GzipFile(fileobj=buffer) as gzipped:
                    decompressed = gzipped.read()
                    if (
                        b"<rss" in decompressed[:500]
                        or b"<feed" in decompressed[:500]
                        or b"<?xml" in decompressed[:500]
                    ):
                        logger.debug(
                            "Gzip decompression successful with %d byte offset", i
                        )
                        return decompressed
        except Exception:
            pass

        # Try zlib with offset
        try:
            decompressed = zlib.decompress(content[i:], 31)
            if (
                b"<rss" in decompressed[:500]
                or b"<feed" in decompressed[:500]
                or b"<?xml" in decompressed[:500]
            ):
                logger.debug(
                    "Zlib decompression successful with %d byte offset and window bits 31",
                    i,
                )
                return decompressed
        except Exception:
            pass

    # If nothing worked, try Highly Scalable Blog specific decompression
    # (known to use a specific compression format)
    if url == "https://highlyscalable.wordpress.com/feed/":
        try:
            # Try with various offsets and window bits
            for i in range(5):
                for window_bits in [47, 31, 15, -15]:
                    try:
                        decompressed = zlib.decompress(content[i:], window_bits)
                        if (
                            b"<rss" in decompressed[:500]
                            or b"<feed" in decompressed[:500]
                            or b"<?xml" in decompressed[:500]
                        ):
                            logger.debug(
                                "Highly Scalable Blog decompression successful with %d byte offset and window bits %d",
                                i,
                                window_bits,
                            )
                            return decompressed
                    except Exception:
                        pass
        except Exception:
            pass

    # Log debugging info about the content
    hex_preview = " ".join(f"{b:02x}" for b in content[:32])
    logger.debug("Failed to decompress content. First 32 bytes: %s", hex_preview)

    # As a last resort, try to extract any XML-like content
    xml_pattern = re.compile(
        b"<\\?xml.*?>.*?<(rss|feed|rdf:RDF)", re.DOTALL | re.IGNORECASE
    )
    match = xml_pattern.search(content)
    if match:
        start_idx = match.start()
        logger.debug("Found XML-like content starting at byte %d", start_idx)
        return content[start_idx:]

    # If all attempts fail, return the original content
    return content


@pydantic.validate_call(validate_return=True)
def get_feed_urls(feed_file: str) -> List[str]:
    """
    Extract feed urls from a json file containing a list of items 'url'.
    It detects whether the file is local or on S3.
    """
    url_list = []
    text_data = ""
    if feed_file.startswith("s3://"):
        bucket, feed_file = feed_file[5:].split("/", 1)
        text_data = (
            boto3.client("s3")
            .get_object(Bucket=bucket, Key=feed_file)
            .get("Body")
            .read()
            .decode("utf-8")
        )
    else:
        text_data = files("rss_email").joinpath(feed_file).read_text()
    data = json.loads(text_data)
    for i in data["feeds"]:
        if "url" in i:
            url_list.append(i["url"])
    return url_list


class Article(BaseModel):
    """
    RSS Article model.
    """

    title: str = Field(min_length=1)
    link: HttpUrl
    description: Optional[str] = ""
    pubdate: datetime

    def __lt__(self, other):
        return self.pubdate < other.pubdate


@pydantic.validate_call(validate_return=True)
def get_feed(url: str, item: bytes, update_date: datetime) -> List[Article]:
    """Get items from defined feed for a given period of time."""
    feed_list = feedparser.parse(item)
    articles = []

    for article in feed_list.entries:
        if (
            hasattr(article, "published_parsed")
            and article.published_parsed is not None
        ):
            feed_date = article.published_parsed
        elif hasattr(article, "updated_parsed") and article.updated_parsed is not None:
            feed_date = article.updated_parsed
        else:
            break
        feed_datetime = datetime.fromtimestamp(mktime(feed_date))
        if feed_datetime > update_date:
            out_article = Article(
                title=article.title,
                link=article.link,
                pubdate=feed_datetime,
            )

            if hasattr(article, "summary"):
                out_article.description = article["summary"]
            elif hasattr(article, "description"):
                out_article.description = article["description"]
            articles.append(out_article)

    if not articles:
        logger.debug("Feed %s contains no new items", url)
    else:
        logger.debug("Feed %s contains %s", url, len(articles))
    return articles


@pydantic.validate_call(validate_return=True)
def get_update_date(days: int = DAYS_OF_NEWS) -> datetime:
    """Get 3 days old RSS if no date/time available..."""
    time_three_days_ago = datetime.now() - timedelta(days)
    lookback_date = datetime(
        time_three_days_ago.year, time_three_days_ago.month, time_three_days_ago.day
    )

    return lookback_date


@pydantic.validate_call(validate_return=True)
def generate_rss(articles: List[Article]) -> str:
    """Generate RSS output."""
    output = []
    if not articles:
        return ""
    output_list = []
    for source_article in articles:
        if source_article not in output_list:
            output_list.append(source_article)
    for article in output_list:
        output.append(
            PyRSS2Gen.RSSItem(
                title=article.title,
                link=str(article.link),
                description=article.description,
                guid=PyRSS2Gen.Guid(str(article.link)),
                pubDate=article.pubdate,
            )
        )

    rss = PyRSS2Gen.RSS2(
        title="Daily Feed",
        link="http://www.greatnews.com",
        description="The news to use...",
        lastBuildDate=datetime.now(),
        items=output,
    )

    logger.debug("Found %s items", len(output))
    return rss.to_xml(CHARACTER_ENCODING)


@pydantic.validate_call(validate_return=True)
def is_connected() -> bool:
    """Check if there is an internet connection."""
    try:
        # see if we can resolve the host name -- tells us if there is
        # a DNS listening
        host = socket.gethostbyname(REMOTE_SERVER)
        # connect to the host -- tells us if the host is actually
        # reachable
        socket.create_connection((host, 80), 2)
        return True
    except timeout:
        logger.warning("No internet connection")
    return False


@pydantic.validate_call(validate_return=True)
def retrieve_rss_feeds(feed_file: str, update_date: datetime) -> str:
    """Run main orchestration function."""
    # Check there is an intenet connection, otherwise bail

    if not is_connected():
        logger.debug("No internet connection")
        sys.exit(0)

    rss_urls = get_feed_urls(feed_file)

    rss_items = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Start the load operations and mark each future with its URL
        future_to_url = {
            executor.submit(get_feed_items, url, update_date): url for url in rss_urls
        }
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                data = future.result()
                rss_items[url] = data
            except Exception as exc:  # pylint: disable=W0718
                logger.warning("%r generated an exception: %s", url, exc)

    filtered_entries = []
    for item_url, item in rss_items.items():
        for f_item in get_feed(item_url, item, update_date):
            filtered_entries.append(f_item)
    return generate_rss(sorted(filtered_entries, reverse=True))


@pydantic.validate_call(validate_return=True)
def create_rss(event: Dict[str, Any], context: Optional[Any] = None) -> None:  # pylint: disable=unused-argument
    """
    Entry point for Lambda.

    Copy generated RSS XML to S3 bucket.

    Expects environment variables set for BUCKET and KEY.
    """
    update_date = get_update_date(DAYS_OF_NEWS)
    logger.debug("Uploading RSS content to S3 bucket")
    bucket = os.environ["BUCKET"]
    key = os.environ["KEY"]
    feeds_file = os.environ["FEED_DEFINITIONS_FILE"]
    content = retrieve_rss_feeds(feeds_file, update_date)
    try:
        boto3.client("s3").put_object(
            Key=key,
            Body=content,
            Bucket=bucket,
            ContentType=f"application/rss+xml; charset={CHARACTER_ENCODING}",
            ContentEncoding=CHARACTER_ENCODING,
        )
        logger.debug("RSS file uploaded to the S3 bucket")
    except ClientError as exc:
        logging.error(exc)
        logging.error(
            """Error uploading object %s from bucket %s.
            Make sure they exist and your bucket is in the same region as this function.""",
            key,
            bucket,
        )
        raise exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grabs a bunch of RSS feeds")
    parser.add_argument(
        "feed_file",
        metavar="I",
        type=str,
        help="JSON file containing a list of names/urls, e.g. ./feed_urls.json",
    )
    args = parser.parse_args()
    ch = logging.StreamHandler(sys.stdout)
    # ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)
    retrieval_date = get_update_date()

    print(retrieve_rss_feeds(args.feed_file, retrieval_date))
