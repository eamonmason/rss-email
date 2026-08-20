"""Persist a rolling window of recent RSS Brief days to S3.

This gives the brief synthesis prompt (see ``brief_generator.py``) enough
"previously covered" context to avoid repeating stories and to frame
multi-day stories as developments instead of new news. It is deliberately
not a database or a vector store: at one run/day and roughly a hundred
articles/day, a 14-day window is a couple thousand small records - a single
compressed-free JSON object in the existing S3 bucket, read once and
rewritten once per run, is simpler and cheaper than any dedicated memory
infrastructure at this scale.

Every public function here is best-effort and never raises: a memory
failure must never break the brief or the digest, so ``load_memory`` and
``save_memory`` swallow their own S3/parse errors and log instead.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import boto3
import pydantic
from botocore.exceptions import ClientError

from .models import (
    BriefMemory,
    BriefMemoryArticle,
    BriefMemoryDay,
    BriefMemoryTheme,
    BriefSynthesis,
)

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_KEY = "brief-memory/memory.json"
DEFAULT_WINDOW_DAYS = 14


@pydantic.validate_call(validate_return=True)
def load_memory(bucket: str, key: str = DEFAULT_MEMORY_KEY) -> BriefMemory:
    """Load the rolling brief memory from S3.

    Returns an empty ``BriefMemory`` (never raises) when the object is
    missing, unreadable, or fails validation - the first run after this
    feature ships, and any corruption, just starts memory fresh.
    """
    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
        data = json.loads(response["Body"].read().decode("utf-8"))
        return BriefMemory(**data)
    except (ClientError, json.JSONDecodeError, pydantic.ValidationError, TypeError) as exc:
        logger.info(
            "No usable brief memory at s3://%s/%s (%s); starting fresh", bucket, key, exc
        )
        return BriefMemory()


@pydantic.validate_call
def save_memory(bucket: str, memory: BriefMemory, key: str = DEFAULT_MEMORY_KEY) -> None:
    """Write the brief memory back to S3. Logs and swallows any failure."""
    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=memory.model_dump_json().encode("utf-8"),
            ContentType="application/json",
        )
    except ClientError as exc:
        logger.warning("Failed to save brief memory to s3://%s/%s: %s", bucket, key, exc)


@pydantic.validate_call(config={"arbitrary_types_allowed": True}, validate_return=True)
def build_day_record(
    brief: BriefSynthesis, article_index: Dict[str, Dict[str, str]], date: str
) -> BriefMemoryDay:
    """Reduce a validated brief synthesis to a compact memory record.

    ``article_index`` is the ``id -> {title, url, source}`` map returned by
    ``brief_generator.build_article_index`` for the same synthesis run -
    themes cite articles by id, so this resolves those ids to the
    title/link a future prompt can display.
    """
    themes: List[BriefMemoryTheme] = []
    for category, cat_data in brief.categories.items():
        for theme in cat_data.themes:
            articles = [
                BriefMemoryArticle(
                    title=article_index[article_id]["title"],
                    link=article_index[article_id].get("url", ""),
                )
                for article_id in theme.top_articles
                if article_id in article_index and article_index[article_id].get("title")
            ]
            themes.append(
                BriefMemoryTheme(
                    category=category,
                    theme=theme.theme,
                    signal_strength=theme.signal_strength,
                    tldr=theme.tldr,
                    articles=articles,
                )
            )
    return BriefMemoryDay(date=date, themes=themes)


@pydantic.validate_call(validate_return=True)
def append_and_prune(
    memory: BriefMemory, day: BriefMemoryDay, window_days: int = DEFAULT_WINDOW_DAYS
) -> BriefMemory:
    """Add/replace ``day`` and drop anything older than ``window_days``.

    Replacing same-date entries makes re-runs for a given day idempotent
    rather than appending duplicates.
    """
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    days = [d for d in memory.days if d.date != day.date and d.date >= cutoff]
    days.append(day)
    days.sort(key=lambda d: d.date)
    return BriefMemory(days=days)


@pydantic.validate_call(validate_return=True)
def render_previous_context(memory: BriefMemory) -> str:
    """Render prior days' themes as a compact text block for the prompt.

    Returns ``""`` when there is no memory yet, so the caller can cleanly
    omit the previous-context section of the prompt.
    """
    blocks = []
    for day in memory.days:
        if not day.themes:
            continue
        lines = [f"## {day.date}"]
        for theme in day.themes:
            lines.append(
                f"- [{theme.category}] ({theme.signal_strength}) "
                f"{theme.theme}: {theme.tldr}"
            )
            for article in theme.articles:
                lines.append(f"    - {article.title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
