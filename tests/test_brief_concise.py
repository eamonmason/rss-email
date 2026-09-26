"""Tests for the RSS Brief's must-read list, length caps, and input filters."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rss_email.brief_generator import (
    FIDELITY_RULE,
    build_prompt,
    build_synthesis_input,
    is_sponsored,
    render_brief_html,
    synthesize,
    _enforce_caps,
    _parse_synthesis,
)
from rss_email.brief_memory import build_day_record, seen_links
from rss_email.models import BriefMemory, BriefSynthesis
from rss_email.retrieve_and_send_email import _maybe_send_brief

CONFIG = {
    "reader_profile": "profile",
    "personal_interests": "interests",
    "model": "model",
    "themed_categories": ["AI/ML", "Technology"],
    "personal_categories": ["Cycling"],
    "prioritised_sources": [],
    "deprioritised_sources": [],
}

INDEX = {
    "1": {"title": "SAML critique", "url": "https://blog.example/saml", "source": "Blog"},
    "2": {"title": "CI rebuilt", "url": "https://linear.example/ci", "source": "Linear"},
    "3": {"title": "No link", "url": "", "source": "Feed"},
}


def _theme(name, ids):
    return {
        "theme": name,
        "signal_strength": "GENERAL",
        "tldr": "t",
        "top_articles": ids,
        "relevance_to_reader": None,
    }


def _stream_client(text):
    client = MagicMock()
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.get_final_text.return_value = text
    client.messages.stream.side_effect = [cm]
    return client


# --- prompt -----------------------------------------------------------------


def test_prompt_has_date_caps_fidelity_and_no_verdict():
    """Today's date, the configured caps and the fidelity rules reach the prompt."""
    prompt = build_prompt(
        {"AI/ML": [{"id": "1", "title": "T", "summary": "", "source": "Blog"}]},
        {**CONFIG, "max_total_themes": 9, "must_read_max": 6},
        date="2026-09-23",
    )
    flat = " ".join(prompt.split())
    assert "TODAY: 2026-09-23" in prompt
    assert "At most 9 themes in total" in flat
    assert "the 5-6 articles" in flat
    assert FIDELITY_RULE in prompt
    assert "an agreement to acquire is not an acquisition" in prompt
    assert "week_verdict" not in prompt
    assert "{" + "MAX_TOTAL_THEMES}" not in prompt


# --- parsing and caps ---------------------------------------------------------


def test_parse_synthesis_reads_must_read():
    """must_read is popped from the payload, not mistaken for a category."""
    payload = {
        "must_read": [{"id": "1", "why": "Best SSO background."}, {"id": 2, "why": "w"}],
        "AI/ML": {"themes": [_theme("A", ["2"])]},
    }
    brief = _parse_synthesis(json.dumps(payload), ["AI/ML"])
    assert [item.id for item in brief.must_read] == ["1", "2"]
    assert "must_read" not in brief.categories


def test_enforce_caps_per_category_total_and_lists():
    """Themes are capped per category then in total; empty categories vanish."""
    brief = BriefSynthesis(
        must_read=[{"id": str(i), "why": ""} for i in range(10)],
        categories={
            "AI/ML": {"themes": [_theme(f"a{i}", []) for i in range(5)]},
            "Technology": {"themes": [_theme(f"t{i}", []) for i in range(5)]},
            "Business": {"themes": [_theme("b", [])]},
        },
        cross_cutting=[{"signal": str(i)} for i in range(5)],
    )
    capped = _enforce_caps(
        brief,
        {"max_themes_per_category": 3, "max_total_themes": 5, "must_read_max": 8,
         "cross_cutting_max": 2},
    )
    assert [t.theme for t in capped.categories["AI/ML"].themes] == ["a0", "a1", "a2"]
    assert [t.theme for t in capped.categories["Technology"].themes] == ["t0", "t1"]
    assert "Business" not in capped.categories
    assert len(capped.must_read) == 8
    assert len(capped.cross_cutting) == 2


def test_synthesize_applies_caps():
    """A model that ignores the caps still yields a capped brief."""
    payload = {"AI/ML": {"themes": [_theme(f"a{i}", []) for i in range(6)]}}
    brief = synthesize(
        {"AI/ML": [{"title": "x", "url": "u", "summary": "s", "source": "Blog"}]},
        CONFIG,
        client=_stream_client(json.dumps(payload)),
    )
    assert len(brief.categories["AI/ML"].themes) == 3


# --- rendering ----------------------------------------------------------------


def test_must_read_renders_first_with_reason_and_drops_unknown_ids():
    """The reading list leads the brief; display text comes from the index."""
    brief = BriefSynthesis(
        must_read=[
            {"id": "2", "why": "Most useful piece for the platform remit."},
            {"id": "99", "why": "Hallucinated."},
            {"id": "2", "why": "Duplicate."},
        ],
        categories={"AI/ML": {"themes": [_theme("A", ["1"])]}},
    )
    body = render_brief_html(brief, INDEX, 3, themed_order=["AI/ML"])
    assert body.index("Read these") < body.index(">AI/ML</h2>")
    assert "[Linear] CI rebuilt</a>" in body
    assert "Most useful piece for the platform remit." in body
    assert "Hallucinated." not in body
    assert "Duplicate." not in body


def test_article_cited_by_two_themes_is_listed_once():
    """The same article under two themes (or in Personal) is only listed once."""
    brief = BriefSynthesis(
        categories={
            "AI/ML": {"themes": [_theme("A", ["1"])]},
            "Technology": {"themes": [_theme("B", ["1", "2"])]},
        },
        personal={"top_stories": ["2", "3"], "summary": ""},
    )
    body = render_brief_html(brief, INDEX, 3)
    assert body.count("[Blog] SAML critique") == 1
    assert body.count("[Linear] CI rebuilt") == 1
    assert "[Feed] No link" in body


def test_verdict_is_not_rendered():
    """week_verdict from an older response shape is ignored by the renderer."""
    brief = BriefSynthesis(
        categories={"AI/ML": {"week_verdict": "VERDICT", "themes": [_theme("A", [])]}}
    )
    assert "VERDICT" not in render_brief_html(brief, INDEX, 1)


# --- input filtering ----------------------------------------------------------


@pytest.mark.parametrize(
    "title, url, expected",
    [
        ("Governing AI agents", "https://www.theregister.com/sponsored/2026/09/x/", True),
        ("Agents", "https://www.forbes.com/sites/brandvoice/2026/x", True),
        ("Sponsored: Cloud costs", "https://example.com/a", True),
        ("[Sponsored] Cloud costs", "https://example.com/a", True),
        ("Why sponsored content fails", "https://example.com/sponsorship-news", False),
        ("SAML critique", "https://blog.trailofbits.com/2026/09/21/saml", False),
    ],
)
def test_is_sponsored(title, url, expected):
    """Paid content is detected by URL path segment or title prefix."""
    assert is_sponsored(title, url) is expected


def test_build_synthesis_input_filters_sponsored_seen_and_strips_tokens():
    """Sponsored and already-featured articles are dropped; tokens are stripped."""
    categories = {
        "AI/ML": [
            {"title": "Ad", "link": "https://www.theregister.com/sponsored/x"},
            {"title": "Old news", "link": "https://example.com/old?utm_source=rss"},
            {
                "title": "Paid feed post",
                "link": "https://stratechery.com/p/?access_token=secret",
                "comments": "https://forum.example.com/t?token=t&page=2",
            },
        ]
    }
    result = build_synthesis_input(
        categories, ["AI/ML"], [], seen_links={"https://example.com/old"}
    )
    items = result["AI/ML"]
    assert [item["title"] for item in items] == ["Paid feed post"]
    assert items[0]["url"] == "https://stratechery.com/p/"
    assert items[0]["comments"] == "https://forum.example.com/t?page=2"


# --- memory -------------------------------------------------------------------


def test_memory_records_must_reads_and_exposes_seen_links():
    """Must-read and theme articles both feed tomorrow's repeat filter."""
    brief = BriefSynthesis(
        must_read=[{"id": "2", "why": "w"}],
        categories={"AI/ML": {"themes": [_theme("A", ["1", "3"])]}},
    )
    day = build_day_record(brief, INDEX, "2026-09-22")
    assert [a.title for a in day.must_read] == ["CI rebuilt"]
    links = seen_links(BriefMemory(days=[day]))
    assert links == {"https://blog.example/saml", "https://linear.example/ci"}


def test_old_memory_without_must_read_still_loads():
    """Memory objects written before must_read existed validate unchanged."""
    memory = BriefMemory(**{"days": [{"date": "2026-09-01", "themes": []}]})
    assert memory.days[0].must_read == []
    assert seen_links(memory) == set()


@patch("rss_email.retrieve_and_send_email.save_memory")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.generate_brief_full")
@patch("rss_email.retrieve_and_send_email.load_memory")
def test_maybe_send_brief_passes_seen_links(mock_load, mock_generate, _send, _save):
    """The send flow hands memory's featured links to the brief generator."""
    brief = BriefSynthesis(categories={"AI/ML": {"themes": [_theme("A", ["1"])]}})
    mock_load.return_value = BriefMemory(days=[build_day_record(brief, INDEX, "2026-09-20")])
    mock_generate.return_value = None

    _maybe_send_brief({"AI/ML": []}, MagicMock(), "to@x", "from@x", "bucket")

    assert mock_generate.call_args.kwargs["seen_links"] == {"https://blog.example/saml"}
