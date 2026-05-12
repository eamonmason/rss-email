"""Tests for the grouping (stage 1) module."""

import json
from unittest.mock import MagicMock, patch

from rss_email.article_grouper import (
    create_grouping_prompt,
    group_articles_with_claude,
    parse_grouping_response,
)
from rss_email.article_processor import ClaudeRateLimiter


def _articles():
    return [
        {
            "title": "OpenAI launches GPT-5",
            "link": "https://a.example/gpt5",
            "description": "OpenAI announced GPT-5 today.",
            "pubDate": "Mon, 26 May 2025 08:00:00 GMT",
        },
        {
            "title": "GPT-5 launch: what's new",
            "link": "https://b.example/gpt5",
            "description": "Coverage of the GPT-5 launch.",
            "pubDate": "Mon, 26 May 2025 09:00:00 GMT",
        },
        {
            "title": "Tour de France route revealed",
            "link": "https://c.example/tdf",
            "description": "2025 Tour de France route announced.",
            "pubDate": "Mon, 26 May 2025 10:00:00 GMT",
        },
    ]


def test_grouping_prompt_mentions_same_event_rule():
    """Prompt instructs Claude to group only on identical events."""
    prompt = create_grouping_prompt(_articles())

    assert "SAME SPECIFIC" in prompt
    assert "KEEP ARTICLES SEPARATE" in prompt
    assert "article_0" in prompt
    assert "article_2" in prompt


def test_parse_grouping_returns_indices_in_order():
    """Valid grouping JSON parses into index lists."""
    response = json.dumps({
        "groups": [["article_0", "article_1"], ["article_2"]],
        "article_count": 3,
    })

    groups = parse_grouping_response(response, article_count=3)

    assert groups == [[0, 1], [2]]


def test_parse_grouping_fills_missing_indices_as_singletons():
    """Any article id not echoed by Claude becomes a singleton group."""
    response = json.dumps({"groups": [["article_0"]], "article_count": 3})

    groups = parse_grouping_response(response, article_count=3)

    # The unreferenced article_1 and article_2 each get their own singleton
    assert [0] in groups
    assert [1] in groups
    assert [2] in groups
    assert len(groups) == 3


def test_parse_grouping_handles_invalid_ids():
    """Bad ids are skipped, but the affected articles still surface as singletons."""
    response = json.dumps({
        "groups": [["not-an-id", "article_0"], ["article_x"]],
        "article_count": 2,
    })

    groups = parse_grouping_response(response, article_count=2)

    # article_0 should still appear (in its partial group), article_1 as singleton
    assert any(0 in g for g in groups)
    assert any(g == [1] for g in groups)


def test_parse_grouping_handles_bad_json_with_singleton_fallback():
    """Completely invalid JSON falls back to one singleton per article."""
    groups = parse_grouping_response("{not json", article_count=3)

    assert groups == [[0], [1], [2]]


@patch("rss_email.article_grouper.anthropic.Anthropic")
@patch("rss_email.article_grouper.get_anthropic_api_key", return_value="test-key")
def test_group_articles_with_claude_happy_path(_mock_key, mock_anthropic):
    """A well-formed Claude response yields the same groups."""
    mock_client = MagicMock()
    mock_anthropic.return_value = mock_client
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = json.dumps({
        "groups": [["article_0", "article_1"], ["article_2"]],
        "article_count": 3,
    })
    response.usage.input_tokens = 200
    response.usage.output_tokens = 100
    mock_client.messages.create.return_value = response

    result = group_articles_with_claude(_articles(), ClaudeRateLimiter())

    assert result == [[0, 1], [2]]


def test_group_articles_with_claude_disabled_returns_singletons(monkeypatch):
    """When CLAUDE_ENABLED is false the function returns singleton groups locally."""
    monkeypatch.setenv("CLAUDE_ENABLED", "false")

    result = group_articles_with_claude(_articles(), ClaudeRateLimiter())

    assert result == [[0], [1], [2]]
