#!/usr/bin/env python3
"""Lambda function to aggregate multiple RSS feeds into a single one."""

from __future__ import annotations, print_function

import argparse
import concurrent.futures
import contextlib
import json
import logging
import os
import socket
import sys
import urllib.request
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
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36"
    )

    req_check_new = urllib.request.Request(
        url,
        data=None,
        headers={
            "User-Agent": user_agent,
            "If-Modified-Since": timestamp.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
    )

    req_retrieve = urllib.request.Request(
        url, data=None, headers={"User-Agent": user_agent}
    )

    try:
        with contextlib.closing(urllib.request.urlopen(req_check_new, timeout=5)):
            with urllib.request.urlopen(req_retrieve, timeout=5) as conn:
                feed_items = conn.read()

    except HTTPError as error:
        if error.code == 304:
            logger.debug("URL: %s not modified in 3 days", url)
        else:
            logger.error("URL: %s, data not retrieved because %s", url, error)
    except URLError as error:
        logger.error("URL: %s, url error %s", url, error)
    except timeout:
        logger.error("socket timed out - URL %s", url)
    else:
        if not feed_items:
            logger.debug("URL: %s - no feed items", url)
    return feed_items


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
