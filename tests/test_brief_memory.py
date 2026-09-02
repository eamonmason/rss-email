"""Tests for the rolling RSS Brief memory store (brief_memory.py)."""
# pylint: disable=redefined-outer-name,unused-argument

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from rss_email.brief_memory import (
    append_and_prune,
    build_day_record,
    load_memory,
    render_previous_context,
    save_memory,
)
from rss_email.models import (
    BriefCategory,
    BriefMemory,
    BriefMemoryArticle,
    BriefMemoryDay,
    BriefMemoryTheme,
    BriefSynthesis,
    BriefTheme,
)


# --- load_memory ------------------------------------------------------------


@patch("rss_email.brief_memory.boto3.client")
def test_load_memory_missing_key_returns_empty(mock_boto3_client):
    """A NoSuchKey/ClientError on get_object -> empty memory, no raise."""
    mock_s3 = MagicMock()
    mock_s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
    )
    mock_boto3_client.return_value = mock_s3

    memory = load_memory("test-bucket", "brief-memory/memory.json")

    assert memory == BriefMemory()


@patch("rss_email.brief_memory.boto3.client")
def test_load_memory_corrupt_json_returns_empty(mock_boto3_client):
    """Unparseable body -> empty memory, no raise."""
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"not json")}
    mock_boto3_client.return_value = mock_s3

    memory = load_memory("test-bucket", "brief-memory/memory.json")

    assert memory == BriefMemory()


@patch("rss_email.brief_memory.boto3.client")
def test_load_memory_happy_path(mock_boto3_client):
    """A valid stored object round-trips into a BriefMemory."""
    stored = {
        "days": [
            {
                "date": "2026-08-18",
                "themes": [
                    {
                        "category": "AI/ML",
                        "theme": "Open weights close the gap",
                        "signal_strength": "HIGH",
                        "tldr": "Details.",
                        "articles": [{"title": "A", "link": "https://x/a"}],
                    }
                ],
            }
        ]
    }
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(stored).encode("utf-8"))
    }
    mock_boto3_client.return_value = mock_s3

    memory = load_memory("test-bucket", "brief-memory/memory.json")

    assert len(memory.days) == 1
    assert memory.days[0].date == "2026-08-18"
    assert memory.days[0].themes[0].theme == "Open weights close the gap"


# --- save_memory -------------------------------------------------------------


@patch("rss_email.brief_memory.boto3.client")
def test_save_memory_writes_json(mock_boto3_client):
    """save_memory puts a JSON-serialised BriefMemory to the given key."""
    mock_s3 = MagicMock()
    mock_boto3_client.return_value = mock_s3
    memory = BriefMemory(
        days=[BriefMemoryDay(date="2026-08-19", themes=[])]
    )

    save_memory("test-bucket", memory, "brief-memory/memory.json")

    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == "brief-memory/memory.json"
    body = json.loads(call_kwargs["Body"].decode("utf-8"))
    assert body["days"][0]["date"] == "2026-08-19"


@patch("rss_email.brief_memory.boto3.client")
def test_save_memory_swallows_client_error(mock_boto3_client):
    """A put_object failure is logged, not raised."""
    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "PutObject"
    )
    mock_boto3_client.return_value = mock_s3

    save_memory("test-bucket", BriefMemory())  # must not raise


# --- build_day_record ---------------------------------------------------------


def test_build_day_record_resolves_ids_to_titles():
    """Theme top_articles ids are resolved to title/link via article_index."""
    synthesis = BriefSynthesis(
        categories={
            "AI/ML": BriefCategory(
                week_verdict="v",
                themes=[
                    BriefTheme(
                        theme="Open weights close the gap",
                        signal_strength="HIGH",
                        tldr="Details.",
                        top_articles=["1", "2"],
                    )
                ],
            )
        }
    )
    article_index = {
        "1": {"title": "Article One", "url": "https://x/1", "source": "HN"},
        "2": {"title": "Article Two", "url": "https://x/2", "source": "HN"},
    }

    day = build_day_record(synthesis, article_index, "2026-08-19")

    assert day.date == "2026-08-19"
    assert len(day.themes) == 1
    theme = day.themes[0]
    assert theme.category == "AI/ML"
    assert [a.title for a in theme.articles] == ["Article One", "Article Two"]
    assert theme.articles[0].link == "https://x/1"


def test_build_day_record_skips_unknown_ids():
    """An id absent from article_index (or with no title) is dropped, not erroring."""
    synthesis = BriefSynthesis(
        categories={
            "AI/ML": BriefCategory(
                themes=[
                    BriefTheme(
                        theme="Theme",
                        signal_strength="GENERAL",
                        top_articles=["99"],
                    )
                ]
            )
        }
    )
    day = build_day_record(synthesis, {}, "2026-08-19")
    assert day.themes[0].articles == []


# --- append_and_prune ---------------------------------------------------------


@patch("rss_email.brief_memory.datetime")
def test_append_and_prune_adds_new_day(mock_datetime):
    """A brand-new date is appended."""
    from datetime import datetime as real_datetime  # pylint: disable=import-outside-toplevel

    mock_datetime.now.return_value = real_datetime(2026, 8, 19)
    memory = BriefMemory(days=[BriefMemoryDay(date="2026-08-18", themes=[])])
    updated = append_and_prune(
        memory, BriefMemoryDay(date="2026-08-19", themes=[]), window_days=14
    )
    assert [d.date for d in updated.days] == ["2026-08-18", "2026-08-19"]


@patch("rss_email.brief_memory.datetime")
def test_append_and_prune_replaces_same_date(mock_datetime):
    """Re-running for a date already in memory replaces it (idempotent)."""
    from datetime import datetime as real_datetime  # pylint: disable=import-outside-toplevel

    mock_datetime.now.return_value = real_datetime(2026, 8, 19)
    memory = BriefMemory(
        days=[
            BriefMemoryDay(
                date="2026-08-19",
                themes=[BriefMemoryTheme(category="c", theme="old", signal_strength="GENERAL")],
            )
        ]
    )
    updated = append_and_prune(
        memory,
        BriefMemoryDay(
            date="2026-08-19",
            themes=[BriefMemoryTheme(category="c", theme="new", signal_strength="HIGH")],
        ),
        window_days=14,
    )
    assert len(updated.days) == 1
    assert updated.days[0].themes[0].theme == "new"


@patch("rss_email.brief_memory.datetime")
def test_append_and_prune_drops_days_outside_window(mock_datetime):
    """Days older than window_days are dropped."""
    from datetime import datetime as real_datetime  # pylint: disable=import-outside-toplevel

    mock_datetime.now.return_value = real_datetime(2026, 8, 19)
    memory = BriefMemory(
        days=[
            BriefMemoryDay(date="2026-07-01", themes=[]),  # well outside a 14-day window
            BriefMemoryDay(date="2026-08-10", themes=[]),  # inside
        ]
    )
    updated = append_and_prune(
        memory, BriefMemoryDay(date="2026-08-19", themes=[]), window_days=14
    )
    dates = [d.date for d in updated.days]
    assert "2026-07-01" not in dates
    assert "2026-08-10" in dates
    assert "2026-08-19" in dates


# --- render_previous_context ---------------------------------------------------


def test_render_previous_context_empty_memory():
    """No days -> empty string, so the prompt can omit the section."""
    assert render_previous_context(BriefMemory()) == ""


def test_render_previous_context_includes_dates_themes_titles():
    """Rendered block surfaces date, category, signal, theme, tldr, and titles."""
    memory = BriefMemory(
        days=[
            BriefMemoryDay(
                date="2026-08-18",
                themes=[
                    BriefMemoryTheme(
                        category="AI/ML",
                        theme="Open weights close the gap",
                        signal_strength="HIGH",
                        tldr="Big shift.",
                        articles=[BriefMemoryArticle(title="Article One", link="https://x/1")],
                    )
                ],
            )
        ]
    )
    rendered = render_previous_context(memory)
    assert "2026-08-18" in rendered
    assert "AI/ML" in rendered
    assert "HIGH" in rendered
    assert "Open weights close the gap" in rendered
    assert "Big shift." in rendered
    assert "Article One" in rendered
