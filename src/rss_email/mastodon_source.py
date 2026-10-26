"""Mastodon home timeline as an article source.

Replaces the standalone mastodon_email_digest job. Posts from the home
timeline that share a link become ordinary articles: the shared page is the
article and the post is its discussion thread, the same shape as Hacker News
or Reddit items. Posts without a link card are skipped - the brief is built
around articles, and a bare post has no title to group or summarise.

Configured by an optional top-level ``mastodon`` object in feed_urls.json,
kept out of ``feeds`` so older deployments ignore it instead of fetching the
instance's home page as an RSS feed::

    "mastodon": {"base_url": "https://mastodon.social", "max_articles": 15}

The access token (``read:statuses`` scope is enough) lives in the SSM
SecureString named by ``MASTODON_TOKEN_PARAMETER``. A missing config or
token disables the source quietly; an API failure logs a warning (which
raises the retrieval alarm) but never blocks the RSS feeds.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl

from .models import RSSItem
from .url_utils import strip_credential_params

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_TOKEN_PARAMETER = "rss-email-mastodon-token"
PAGE_SIZE = 40  # Mastodon's maximum for timelines
MAX_STATUSES = 1000  # Hard stop on pagination, ~25 pages
DESCRIPTION_MAX_LENGTH = 500
REQUEST_TIMEOUT = 20.0


class MastodonConfig(BaseModel):
    """The ``mastodon`` block of feed_urls.json."""

    base_url: HttpUrl
    name: str = "Mastodon"
    enabled: bool = True
    # Kept per 24-hour window, so a Monday run covering the weekend still
    # gets a full day's worth for each day rather than one shared top N.
    max_articles: int = 15


def load_config(feed_data: Dict[str, Any]) -> Optional[MastodonConfig]:
    """Return the enabled Mastodon config from parsed feed JSON, else None."""
    raw = feed_data.get("mastodon")
    if not raw:
        return None
    try:
        config = MastodonConfig(**raw)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Ignoring invalid mastodon config: %s", exc)
        return None
    return config if config.enabled else None


def get_token() -> Optional[str]:
    """Read the access token from SSM, or None if it isn't set up."""
    name = os.environ.get("MASTODON_TOKEN_PARAMETER", DEFAULT_TOKEN_PARAMETER)
    try:
        response = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Mastodon configured but token parameter %s unreadable: %s", name, exc)
        return None
    return response["Parameter"]["Value"]


def fetch_home_timeline(base_url: str, token: str, since: datetime) -> List[Dict[str, Any]]:
    """Page backwards through the home timeline until ``since``."""
    url = f"{base_url.rstrip('/')}/api/v1/timelines/home"
    headers = {"Authorization": f"Bearer {token}"}
    statuses: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {"limit": PAGE_SIZE}
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        while len(statuses) < MAX_STATUSES:
            response = client.get(url, params=params)
            response.raise_for_status()
            page = response.json()
            if not page:
                break
            statuses.extend(page)
            if _created_at(page[-1]) < since:
                break
            params["max_id"] = page[-1]["id"]
    return [s for s in statuses if _created_at(s) >= since]


def _created_at(status: Dict[str, Any]) -> datetime:
    return datetime.fromisoformat(status["created_at"].replace("Z", "+00:00"))


def score(status: Dict[str, Any]) -> float:
    """Engagement relative to audience: the old digest's SimpleWeighted scorer.

    Geometric mean of boosts and favourites (each +1 so a zero doesn't wipe
    out the other), divided by the square root of the author's followers so a
    small account's well-received post can beat a celebrity's routine one.
    """
    boosts = status.get("reblogs_count", 0)
    favourites = status.get("favourites_count", 0)
    followers = status.get("account", {}).get("followers_count", 0)
    if not (boosts or favourites) or followers <= 0:
        return 0.0
    return math.sqrt((boosts + 1) * (favourites + 1)) / math.sqrt(followers)


def _wanted(status: Dict[str, Any]) -> bool:
    """A link post the reader hasn't already acted on or filtered out."""
    card = status.get("card") or {}
    return bool(
        card.get("url")
        and (card.get("title") or "").strip()
        and not status.get("filtered")
        and not status.get("reblogged")
        and not status.get("favourited")
        and not status.get("bookmarked")
    )


def _plain_text(html: str) -> str:
    return BeautifulSoup(html or "", features="html.parser").get_text(" ", strip=True)


def to_article(
    status: Dict[str, Any], seen_at: datetime, config: MastodonConfig
) -> Optional[RSSItem]:
    """Map a link post to an article, or None if the card is unusable."""
    card = status["card"]
    acct = status.get("account", {}).get("acct", "")
    post_text = _plain_text(status.get("content", ""))
    parts = [card.get("description", "").strip(), f"@{acct}: {post_text}" if post_text else ""]
    description = " - ".join(p for p in parts if p)[:DESCRIPTION_MAX_LENGTH]
    try:
        return RSSItem(
            title=card["title"].strip(),
            link=strip_credential_params(card["url"]),
            description=description,
            comments=status.get("url") or None,
            # Naive UTC, like the RSS feed articles it is sorted against.
            pubdate=seen_at.astimezone(timezone.utc).replace(tzinfo=None),
            source_name=config.name,
            source_url=str(config.base_url),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("Skipping Mastodon post %s: %s", status.get("url"), exc)
        return None


def select_articles(
    timeline: List[Dict[str, Any]], config: MastodonConfig, now: datetime
) -> List[RSSItem]:
    """Pick the top link posts per 24-hour window, one article per link.

    The email only sends items newer than its last run, so ranking the whole
    lookback together would fill the quota with yesterday's posts (which have
    had longer to collect boosts) and then drop them downstream.
    """
    best: Dict[str, Tuple[float, int, RSSItem]] = {}
    for entry in timeline:
        status = entry.get("reblog") or entry
        if not _wanted(status):
            continue
        # A boost surfaces when it was boosted, not when it was first posted.
        seen_at = _created_at(entry)
        article = to_article(status, seen_at, config)
        if article is None:
            continue
        window = int((now - seen_at) / timedelta(days=1))
        key = str(article.link)
        candidate = (score(status), window, article)
        if key not in best or candidate[0] > best[key][0]:
            best[key] = candidate

    by_window: Dict[int, List[Tuple[float, RSSItem]]] = {}
    for post_score, window, article in best.values():
        by_window.setdefault(window, []).append((post_score, article))
    selected: List[RSSItem] = []
    for ranked in by_window.values():
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected.extend(article for _, article in ranked[: config.max_articles])
    return selected


def retrieve_mastodon_articles(
    feed_data: Dict[str, Any], since: datetime
) -> Tuple[Optional[str], List[RSSItem]]:
    """Return (source name, articles), or (None, []) when not configured."""
    config = load_config(feed_data)
    if config is None:
        return None, []
    token = get_token()
    if not token:
        return config.name, []
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    try:
        timeline = fetch_home_timeline(str(config.base_url), token, since)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to fetch Mastodon home timeline: %s", exc)
        return config.name, []
    articles = select_articles(timeline, config, datetime.now(timezone.utc))
    logger.info(
        "Mastodon: %d link posts selected from %d timeline entries",
        len(articles), len(timeline),
    )
    return config.name, articles
