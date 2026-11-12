"""Haiku 4.5 / Haiku 5.5 compatibility (claude_compat.py and its call sites)."""
# pylint: disable=missing-function-docstring,missing-class-docstring

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from rss_email.article_grouper import group_articles_with_claude
from rss_email.article_processor import ClaudeRateLimiter, _get_max_tokens_for_model
from rss_email.claude_compat import effort_params, response_text
from rss_email.submit_email_batch import create_batch_requests

ARTICLES = [
    {"title": f"Story {i}", "description": "d", "link": f"https://x/{i}", "published": "now"}
    for i in range(3)
]
GROUPS_JSON = json.dumps({"groups": [["article_0", "article_1"], ["article_2"]], "article_count": 3})


def thinking(text="Let me think."):
    return SimpleNamespace(type="thinking", thinking=text)


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def test_response_text_skips_thinking_blocks():
    message = SimpleNamespace(content=[thinking(), text_block(' {"a": 1} ')])
    assert response_text(message) == '{"a": 1}'


@pytest.mark.parametrize("content", [[], None, [thinking()]])
def test_response_text_empty_when_no_text(content):
    assert response_text(SimpleNamespace(content=content)) == ""


def test_effort_only_for_haiku_5(monkeypatch):
    monkeypatch.delenv("CLAUDE_HAIKU_EFFORT", raising=False)
    assert effort_params("claude-haiku-5-5") == {"output_config": {"effort": "low"}}
    assert not effort_params("claude-haiku-4-5")
    monkeypatch.setenv("CLAUDE_HAIKU_EFFORT", "medium")
    assert effort_params("claude-haiku-5-5") == {"output_config": {"effort": "medium"}}


@pytest.mark.parametrize("model, expected", [
    ("claude-haiku-4-5", 8192), ("claude-haiku-5-5", 8192),
])
def test_max_tokens_for_haiku(model, expected):
    assert _get_max_tokens_for_model(model) == expected


def test_batch_requests_carry_effort_for_haiku_5_only():
    batches = [[("group_0", [0, 1])]]
    new = create_batch_requests(batches, ARTICLES, "claude-haiku-5-5")[0]["params"]
    old = create_batch_requests(batches, ARTICLES, "claude-haiku-4-5")[0]["params"]
    assert new["output_config"] == {"effort": "low"}
    assert "output_config" not in old


def _grouping_response(content, stop_reason="end_turn"):
    response = MagicMock()
    response.content = content
    response.stop_reason = stop_reason
    response.usage.input_tokens = 100
    response.usage.output_tokens = 50
    return response


@patch("rss_email.article_grouper.anthropic.Anthropic")
@patch("rss_email.article_grouper.get_anthropic_api_key", return_value="k")
def test_grouping_parses_when_thinking_block_leads(_key, mock_anthropic, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    client = mock_anthropic.return_value
    client.messages.create.return_value = _grouping_response([thinking(), text_block(GROUPS_JSON)])

    assert group_articles_with_claude(ARTICLES, ClaudeRateLimiter()) == [[0, 1], [2]]
    assert client.messages.create.call_args.kwargs["output_config"] == {"effort": "low"}


@patch("rss_email.article_grouper.anthropic.Anthropic")
@patch("rss_email.article_grouper.get_anthropic_api_key", return_value="k")
def test_grouping_refusal_returns_none_instead_of_crashing(_key, mock_anthropic, monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    mock_anthropic.return_value.messages.create.return_value = _grouping_response([], "refusal")

    assert group_articles_with_claude(ARTICLES, ClaudeRateLimiter()) is None
