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
import json
import logging
import socket
import sys
import threading
import urllib.parse
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from importlib.resources import files
from socket import timeout
from time import mktime
from typing import Dict, List, Optional, Tuple

import boto3
import feedparser
import httpx
import pydantic
from pydantic import BaseModel, Field, HttpUrl

try:
    from .models import RSSItem, FeedList
except ImportError:
    # For local testing or when models module is not available
    RSSItem = None
    FeedList = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REMOTE_SERVER = "www.google.com"
DAYS_OF_NEWS = 3

# Some hosts (notably Reddit) aggressively rate-limit bursts of requests from a
# single IP. Reddit returns 429 the moment a handful of its feeds are fetched
# concurrently (as the thread pool does), so we serialise requests to these
# hosts and space out their starts. Keyed by registrable-domain suffix ->
# minimum seconds between request starts. Within the 5-minute Lambda budget this
# comfortably covers a daily batch of the configured Reddit feeds.
RATE_LIMITED_HOSTS = {"reddit.com": 8.0}

_throttle_registry_lock = threading.Lock()
_host_locks: Dict[str, threading.Lock] = {}
_host_last_start: Dict[str, float] = {}


def _rate_limited_host_key(url: str) -> Optional[str]:
    """Return the ``RATE_LIMITED_HOSTS`` key matching ``url``'s host, else None."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for key in RATE_LIMITED_HOSTS:
        if host == key or host.endswith("." + key):
            return key
    return None


def _throttle_host(url: str) -> None:
    """Serialise and space out requests to known rate-limited hosts (e.g. Reddit).

    No-op for other hosts. When called from the retrieval thread pool this ensures
    only one request to a sensitive host runs at a time, with at least
    ``RATE_LIMITED_HOSTS[key]`` seconds between request starts, so a burst of feeds
    on the same host does not trip 429 Too Many Requests.
    """
    key = _rate_limited_host_key(url)
    if key is None:
        return
    with _throttle_registry_lock:
        lock = _host_locks.setdefault(key, threading.Lock())
    with lock:
        wait = RATE_LIMITED_HOSTS[key] - (time.monotonic() - _host_last_start.get(key, 0.0))
        if wait > 0:
            logger.debug("Throttling %s for %.1fs to respect %s rate limit", url, wait, key)
            time.sleep(wait)
        _host_last_start[key] = time.monotonic()


@pydantic.validate_call(validate_return=True)
def get_feed_items(url: str, timestamp: datetime) -> bytes:
    """Slurps feed url.

    Uses httpx, which auto-decodes gzip/deflate/br responses based on the
    Content-Encoding header. urllib does not do this despite us sending
    ``Accept-Encoding: gzip, deflate, br``, which is why the old implementation
    needed ~300 lines of signature-sniffing decompression fallbacks
    (detect_and_decompress) and SSL/HTTP-downgrade retries; none of that is
    needed once the client actually honours the encoding it negotiated.
    """
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
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        # Some sites check Referer to avoid scraping
        "Referer": "https://www.google.com/",
        # Single conditional GET instead of a throwaway check request + a
        # second full GET (the old code fetched every feed body twice).
        "If-Modified-Since": timestamp.strftime("%a, %d %b %Y %H:%M:%S GMT"),
    }

    max_retries = 3
    retry_delay = 2  # seconds

    # Politely serialise/space requests to rate-limited hosts (e.g. Reddit).
    _throttle_host(url)

    for attempt in range(max_retries):
        try:
            response = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)

            if response.status_code == 304:
                logger.debug("URL: %s not modified in 3 days", url)
                return b""
            if response.status_code == 403:
                # Some sites routinely block scrapers; a single 403 is expected
                # and shouldn't page on-call, so log at INFO like 429 below.
                logger.info(
                    "URL: %s returned 403 Forbidden. This might be a site that aggressively blocks scrapers.",
                    url,
                )
                return b""
            if response.status_code == 429:
                # Rate limited. Retrying within this invocation only makes it
                # worse, so skip and pick the feed up on the next run. Logged at
                # INFO so it does not trigger the error/warning alert.
                logger.info(
                    "URL: %s rate-limited (429); skipping without retry to avoid "
                    "hammering (Retry-After=%s)",
                    url,
                    response.headers.get("Retry-After"),
                )
                return b""

            response.raise_for_status()
            return response.content

        except httpx.HTTPStatusError as error:
            logger.error("URL: %s, data not retrieved because %s", url, error)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
        except httpx.TimeoutException:
            logger.error("socket timed out - URL %s", url)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
        except httpx.HTTPError as error:
            logger.error("URL: %s, url error %s", url, error)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
    return b""


def _load_feed_json(feed_file: str) -> dict:
    """Load feed configuration JSON from a local path or S3 URI."""
    if feed_file.startswith("s3://"):
        bucket, key = feed_file[5:].split("/", 1)
        text_data = (
            boto3.client("s3")
            .get_object(Bucket=bucket, Key=key)
            .get("Body")
            .read()
            .decode("utf-8")
        )
    else:
        text_data = files("rss_email").joinpath(feed_file).read_text()
    return json.loads(text_data)


@pydantic.validate_call(validate_return=True)
def get_feed_urls(feed_file: str) -> List[str]:
    """
    Extract feed urls from a json file containing a list of items 'url'.
    It detects whether the file is local or on S3.
    Uses FeedList model for validation when available.
    """
    url_list = []
    data = _load_feed_json(feed_file)

    # Use FeedList model if available for validation
    if FeedList is not None:
        try:
            feed_list = FeedList.from_json_data(data)
            # Only return URLs for enabled feeds
            for feed in feed_list.feeds:
                if feed.enabled:
                    url_list.append(str(feed.url))
        except Exception as e:
            logger.warning("Failed to parse feed list with FeedList model: %s", str(e))
            # Fallback to original parsing
            for i in data["feeds"]:
                if "url" in i:
                    url_list.append(i["url"])
    else:
        # Original parsing when FeedList is not available
        for i in data["feeds"]:
            if "url" in i:
                url_list.append(i["url"])

    return url_list


@pydantic.validate_call(validate_return=True)
def get_feed_url_to_name(feed_file: str) -> Dict[str, str]:
    """Return a mapping of enabled feed URL -> feed name from the feed JSON."""
    url_to_name: Dict[str, str] = {}
    if FeedList is None:
        return url_to_name
    try:
        data = _load_feed_json(feed_file)
        feed_list = FeedList.from_json_data(data)
        for feed in feed_list.feeds:
            if feed.enabled:
                url_to_name[str(feed.url)] = feed.name
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to load feed name map: %s", str(e))
    return url_to_name


@pydantic.validate_call(validate_return=True)
def get_feed_limits(feed_file: str) -> Dict[str, Dict]:
    """Return per-URL limits {url: {max_articles, lookback_days}} for enabled feeds."""
    limits: Dict[str, Dict] = {}
    if FeedList is None:
        return limits
    try:
        data = _load_feed_json(feed_file)
        feed_list = FeedList.from_json_data(data)
        for feed in feed_list.feeds:
            if feed.enabled:
                limits[str(feed.url)] = {
                    "max_articles": feed.max_articles,
                    "lookback_days": feed.lookback_days,
                }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to load feed limits: %s", str(e))
    return limits


# Use shared RSSItem model if available, otherwise fallback to local Article class
if RSSItem is not None:
    Article = RSSItem
else:
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
def get_feed(
    url: str,
    item: bytes,
    update_date: datetime,
    feed_name: Optional[str] = None,
) -> List[Article]:
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
            continue
        feed_datetime = datetime.fromtimestamp(mktime(feed_date))
        if feed_datetime > update_date:
            # Create article with proper fields based on model type
            article_kwargs = {
                "title": article.title,
                "link": article.link,
                "pubdate": feed_datetime,
            }

            # Add sort_date if using RSSItem
            if RSSItem is not None:
                article_kwargs["sort_date"] = mktime(feed_date)
                if feed_name:
                    article_kwargs["source_name"] = feed_name
                    article_kwargs["source_url"] = url

            # Add comments link if available
            if hasattr(article, "comments"):
                article_kwargs["comments"] = article.comments

            out_article = Article(**article_kwargs)

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
def generate_articles_json(articles: List[Article]) -> str:
    """Serialize deduplicated articles to a JSON array.

    Storing pubDate as its original display string plus the already-known
    sortDate epoch (rather than RSS/XML) means readers never need to
    re-parse a date string to filter by last-run-date.
    """
    if not articles:
        return "[]"
    output_list = []
    for source_article in articles:
        if source_article not in output_list:
            output_list.append(source_article)

    output = []
    for article in output_list:
        item = {
            "title": article.title,
            "link": str(article.link),
            "description": article.description or "",
            "pubDate": article.pubdate.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "sortDate": getattr(article, "sort_date", None) or article.pubdate.timestamp(),
        }
        if article.comments:
            item["comments"] = str(article.comments)
        source_name = getattr(article, "source_name", None)
        source_url = getattr(article, "source_url", None)
        if source_name and source_url:
            item["sourceName"] = source_name
            item["sourceUrl"] = str(source_url)
        output.append(item)

    logger.debug("Found %s items", len(output))
    return json.dumps(output)


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
def retrieve_rss_feeds(feed_file: str, update_date: datetime) -> Tuple[str, Dict[str, int]]:
    """Run main orchestration function. Returns (rss_xml, per_url_article_counts)."""
    # Check there is an intenet connection, otherwise bail

    if not is_connected():
        logger.debug("No internet connection")
        sys.exit(0)

    rss_urls = get_feed_urls(feed_file)
    feed_limits = get_feed_limits(feed_file)
    feed_names = get_feed_url_to_name(feed_file)

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

    per_url_counts: Dict[str, int] = {}
    filtered_entries = []
    for item_url, item in rss_items.items():
        limits = feed_limits.get(item_url, {})
        lookback = limits.get("lookback_days")
        feed_update_date = get_update_date(lookback) if lookback else update_date
        try:
            feed_articles = get_feed(
                item_url, item, feed_update_date, feed_name=feed_names.get(item_url)
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to parse feed %s: %s", item_url, exc)
            per_url_counts[item_url] = 0
            continue
        max_articles = limits.get("max_articles")
        if max_articles is not None:
            feed_articles = sorted(feed_articles, reverse=True)[:max_articles]
        per_url_counts[item_url] = len(feed_articles)
        filtered_entries.extend(feed_articles)
    return generate_articles_json(sorted(filtered_entries, reverse=True)), per_url_counts


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
