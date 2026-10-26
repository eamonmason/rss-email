"""Tests for the brief-sourced narration script (podcast_script.py)."""
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest
from botocore.exceptions import ClientError

from rss_email.models import (
    BriefCategory,
    BriefSynthesis,
    BriefTheme,
    CrossCuttingSignal,
    MustRead,
    PersonalBlock,
)
from rss_email.podcast_script import (
    build_narration_input,
    build_prompt,
    generate_narration,
    load_narration_input,
    render_material,
    save_narration_input,
)


@pytest.fixture
def article_index():
    return {
        "1": {"title": "Chipmaker cuts guidance", "url": "https://x.example/1", "source": "Reuters", "comments": ""},
        "2": {"title": "Zero-day in libfoo", "url": "https://x.example/2?token=secret", "source": "Lobsters",
              "comments": "https://lobste.rs/s/abc"},
        "3": {"title": "Tour stage preview", "url": "https://x.example/3", "source": "Cycling News", "comments": ""},
    }


@pytest.fixture
def synthesis():
    return BriefSynthesis(
        must_read=[MustRead(id="1", why="It resets sector expectations."), MustRead(id="99", why="Unknown id.")],
        categories={
            "Cybersecurity": BriefCategory(themes=[
                BriefTheme(theme="Libfoo zero-day", signal_strength="HIGH", tldr="A flaw was patched.",
                           top_articles=["2"], relevance_to_reader="Check your dependencies."),
            ]),
            "Empty": BriefCategory(themes=[]),
        },
        cross_cutting=[CrossCuttingSignal(signal="Supply chain risk", implication="Pin versions.")],
        personal=PersonalBlock(top_stories=["3"], summary="A big mountain day."),
    )


def test_build_narration_input_resolves_ids_and_drops_urls(synthesis, article_index):
    result = build_narration_input(synthesis, article_index, "2026-10-27")

    assert result["date"] == "2026-10-27"
    assert result["must_read"] == [
        {"title": "Chipmaker cuts guidance", "source": "Reuters", "why": "It resets sector expectations."}
    ]
    assert [c["category"] for c in result["categories"]] == ["Cybersecurity"]
    assert result["personal"]["stories"] == [{"title": "Tour stage preview", "source": "Cycling News"}]
    dumped = json.dumps(result)
    assert "http" not in dumped and "secret" not in dumped


def test_prompt_is_single_narrator_with_fidelity_rule(synthesis, article_index):
    prompt = build_prompt(build_narration_input(synthesis, article_index, "2026-10-27"))

    assert "2026-10-27" in prompt
    assert "Accuracy rules" in prompt
    assert "Chipmaker cuts guidance" in prompt
    assert "Libfoo zero-day" in prompt
    assert "Supply chain risk" in prompt
    assert "Marco" not in prompt and "Joanna" not in prompt
    assert "{" not in prompt.replace("{}", "")  # every placeholder substituted


def test_render_material_omits_empty_sections():
    text = render_material({"date": "d", "must_read": [], "categories": [], "cross_cutting": [], "personal": None})

    assert "CROSS-CUTTING" not in text and "PERSONAL" not in text


def _client_returning(*blocks):
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=list(blocks))
    return client


def test_generate_narration_reads_text_blocks_only():
    thinking = MagicMock(type="thinking", text="ignore me")
    text = MagicMock(type="text", text="  Good morning.  ")

    script = generate_narration({"date": "d"}, _client_returning(thinking, text))

    assert script == "Good morning."


def test_generate_narration_uses_configured_model(monkeypatch):
    monkeypatch.setenv("PODCAST_CLAUDE_MODEL", "claude-test")
    client = _client_returning(MagicMock(type="text", text="Hello."))

    generate_narration({"date": "d"}, client)

    assert client.messages.create.call_args.kwargs["model"] == "claude-test"


def test_generate_narration_api_error_returns_none():
    client = MagicMock()
    client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())

    assert generate_narration({"date": "d"}, client) is None


def test_generate_narration_empty_response_returns_none():
    assert generate_narration({"date": "d"}, _client_returning(MagicMock(type="text", text=" "))) is None


@patch("rss_email.podcast_script.boto3.client")
def test_save_and_load_round_trip(mock_boto3_client):
    s3 = MagicMock()
    mock_boto3_client.return_value = s3
    payload = {"date": "2026-10-27", "must_read": []}

    assert save_narration_input("bucket", payload) is True
    body = s3.put_object.call_args.kwargs["Body"]
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: body)}

    assert load_narration_input("bucket") == payload


@patch("rss_email.podcast_script.boto3.client")
def test_load_missing_returns_none_and_save_failure_returns_false(mock_boto3_client):
    s3 = MagicMock()
    error = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "Op")
    s3.get_object.side_effect = error
    s3.put_object.side_effect = error
    mock_boto3_client.return_value = s3

    assert load_narration_input("bucket") is None
    assert save_narration_input("bucket", {}) is False
