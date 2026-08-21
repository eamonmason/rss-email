"""Tests for the RSS Brief companion email generator."""
# pylint: disable=redefined-outer-name,unused-argument,too-many-positional-arguments

import contextlib
import html
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pydantic
import pytest

import cli_brief_generator
from rss_email.article_processor import ProcessedArticle
from rss_email.brief_generator import (
    BriefResult,
    build_article_index,
    build_prompt,
    build_synthesis_input,
    ensure_article_ids,
    generate_brief,
    generate_brief_full,
    match_title_to_url,
    render_brief_html,
    source_tier,
    synthesize,
    _best_title_match,
    _canonical_category,
    _linkify_or_strip_citations,
    _render_article_links,
    _signal_badge,
)
from rss_email.models import BriefSynthesis, BriefTheme
from rss_email.retrieve_and_send_email import lambda_handler


# Minimal config for synthesize()/build_prompt() in tests.
SYNTH_CONFIG = {
    "reader_profile": "profile",
    "personal_interests": "personal interests",
    "model": "model",
    "major_story_floor": True,
    "themed_categories": ["AI/ML"],
    "personal_categories": ["Cycling"],
    "prioritised_sources": ["Hacker News", "Blog"],
    "deprioritised_sources": ["Techmeme", "Slashdot"],
}


VALID_SYNTHESIS = {
    "AI/ML": {
        "week_verdict": "Models keep getting cheaper.",
        "themes": [
            {
                "theme": "Open-weight models close the gap",
                "signal_strength": "HIGH",
                "tldr": "Open models now rival proprietary ones. This shifts build-vs-buy.",
                "top_articles": ["1", "2"],
                "relevance_to_reader": "Affects your platform tooling choices.",
            },
            {
                "theme": "Minor benchmark updates",
                "signal_strength": "GENERAL",
                "tldr": "Incremental gains.",
                "top_articles": ["3"],
                "relevance_to_reader": None,
            },
        ],
    },
    "cross_cutting": [
        {
            "signal": "AI reshapes security tooling",
            "categories_involved": ["AI/ML", "Cybersecurity"],
            "implication": "Watch for AI-driven SOC tools.",
        }
    ],
    "personal": {
        "top_stories": ["4"],
        "summary": "Cycling season heats up.",
    },
}

# id -> {title, url, source} matching VALID_SYNTHESIS's top_articles/top_stories ids.
VALID_ARTICLE_INDEX = {
    "1": {"title": "Open model beats GPT", "url": "https://x/a", "source": ""},
    "2": {"title": "Llama 4 released", "url": "https://x/b", "source": ""},
    "3": {"title": "Benchmark tweak", "url": "", "source": ""},
    "4": {"title": "Tour de France route announced", "url": "https://x/cycling", "source": ""},
}


def make_client(texts):
    """Return a mock Anthropic client whose messages.stream yields the given texts."""
    client = MagicMock()
    stream_cms = []
    for text in texts:
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.get_final_text.return_value = text
        stream_cms.append(cm)
    client.messages.stream.side_effect = stream_cms
    return client


# --- build_synthesis_input ------------------------------------------------


def test_build_synthesis_input_filters_categories():
    """Only themed and personal categories survive; others are dropped."""
    categories = {
        "AI/ML": [{"title": "A", "link": "https://x/a", "summary": "sa"}],
        "Cycling": [{"title": "C", "link": "https://x/c", "summary": "sc"}],
        "Science": [{"title": "S", "link": "https://x/s", "summary": "ss"}],
    }
    result = build_synthesis_input(categories, ["AI/ML"], ["Cycling"])
    assert set(result.keys()) == {"AI/ML", "Cycling"}
    assert result["AI/ML"][0] == {
        "title": "A",
        "url": "https://x/a",
        "summary": "sa",
        "source": "",
        "id": "1",
    }


def test_build_synthesis_input_assigns_stable_ids():
    """Every article gets a unique, stable numeric-string id."""
    categories = {
        "AI/ML": [
            {"title": "A", "link": "https://x/a", "summary": "sa"},
            {"title": "B", "link": "https://x/b", "summary": "sb"},
        ],
        "Cycling": [{"title": "C", "link": "https://x/c", "summary": "sc"}],
    }
    result = build_synthesis_input(categories, ["AI/ML"], ["Cycling"])
    ids = [item["id"] for item in result["AI/ML"]] + [item["id"] for item in result["Cycling"]]
    assert ids == ["1", "2", "3"]


def test_build_synthesis_input_handles_objects_and_dicts():
    """Accepts both ProcessedArticle objects and raw dicts."""
    article = ProcessedArticle(
        title="Obj", link="https://x/o", summary="so", category="AI/ML", pubdate=""
    )
    categories = {
        "AI/ML": [article, {"title": "Dict", "link": "https://x/d", "summary": "sd"}]
    }
    result = build_synthesis_input(categories, ["AI/ML"], [])
    titles = [item["title"] for item in result["AI/ML"]]
    assert titles == ["Obj", "Dict"]
    assert result["AI/ML"][0]["url"] == "https://x/o"


def test_build_synthesis_input_extracts_source():
    """The feed/source name is captured from objects (sources) and dicts."""
    article = ProcessedArticle(
        title="Obj",
        link="https://x/o",
        summary="so",
        category="AI/ML",
        pubdate="",
        sources=[{"feed_name": "Hacker News", "title": "Obj", "link": "https://x/o",
                  "pubdate": ""}],
    )
    categories = {
        "AI/ML": [
            article,
            {"title": "Dict", "link": "https://x/d", "summary": "sd",
             "sourceName": "Techmeme"},
        ]
    }
    result = build_synthesis_input(categories, ["AI/ML"], [])
    sources = {item["title"]: item["source"] for item in result["AI/ML"]}
    assert sources == {"Obj": "Hacker News", "Dict": "Techmeme"}


# --- ensure_article_ids ----------------------------------------------------


def test_ensure_article_ids_backfills_missing():
    """Articles without an id get one assigned in dict/list order."""
    synthesis_input = {
        "AI/ML": [{"title": "A"}, {"title": "B"}],
        "Cycling": [{"title": "C"}],
    }
    ensure_article_ids(synthesis_input)
    assert [item["id"] for item in synthesis_input["AI/ML"]] == ["1", "2"]
    assert synthesis_input["Cycling"][0]["id"] == "3"


def test_ensure_article_ids_preserves_existing():
    """An article that already has an id keeps it."""
    synthesis_input = {"AI/ML": [{"title": "A", "id": "custom"}]}
    ensure_article_ids(synthesis_input)
    assert synthesis_input["AI/ML"][0]["id"] == "custom"


# --- source tiering -------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Hacker News", "high"),
        ("Simon Willison's Blog", "high"),
        ("Techmeme", "low"),
        ("Slashdot", "low"),
        ("Reuters", "medium"),
        ("", "medium"),
    ],
)
def test_source_tier(name, expected):
    """Source names map to the expected priority tier."""
    assert source_tier(name, SYNTH_CONFIG) == expected


# --- build_prompt ---------------------------------------------------------


def test_build_prompt_includes_source_and_rules():
    """The prompt surfaces sources, the source rule, personal interests, and floor."""
    synthesis_input = {
        "AI/ML": [
            {"title": "Aggregated", "url": "https://x/a", "summary": "sa",
             "source": "Techmeme", "id": "1"},
            {"title": "From HN", "url": "https://x/h", "summary": "sh",
             "source": "Hacker News", "id": "2"},
        ]
    }
    prompt = build_prompt(synthesis_input, SYNTH_CONFIG)
    assert "(2) [Hacker News] From HN" in prompt
    assert "(1) [Techmeme] Aggregated" in prompt
    assert "personal interests" in prompt.lower()
    assert "Source ranking:" in prompt
    assert "never drop a genuinely major story" in prompt
    assert "cite articles ONLY by their numeric ID" in prompt
    # High-tier source is presented before the deprioritised one.
    assert prompt.index("From HN") < prompt.index("Aggregated")


def test_build_prompt_forbids_inline_citations_in_prose():
    """The prompt tells Claude id citations belong only in top_articles/
    top_stories, never inline in tldr/relevance_to_reader/etc."""
    prompt = build_prompt(
        {"AI/ML": [{"title": "T", "url": "u", "summary": "s", "source": "Blog", "id": "1"}]},
        SYNTH_CONFIG,
    )
    assert "never write an id citation" in prompt
    assert "tldr, relevance_to_reader, week_verdict, implication, and" in prompt
    assert "summary are plain-prose fields" in prompt


def test_build_prompt_floor_can_be_disabled():
    """With major_story_floor false, the ruthless rule is used instead."""
    config = {**SYNTH_CONFIG, "major_story_floor": False}
    prompt = build_prompt(
        {"AI/ML": [{"title": "T", "url": "u", "summary": "s", "source": "Blog", "id": "1"}]},
        config,
    )
    assert "never drop a genuinely major story" not in prompt
    assert "Be ruthless with noise" in prompt


def test_build_prompt_omits_previous_context_when_empty():
    """No previous_context -> no "PREVIOUS DAYS" section in the prompt."""
    prompt = build_prompt(
        {"AI/ML": [{"title": "T", "url": "u", "summary": "s", "source": "Blog", "id": "1"}]},
        SYNTH_CONFIG,
    )
    assert "PREVIOUS DAYS" not in prompt


def test_build_prompt_includes_previous_context_and_continuity_rule():
    """A non-empty previous_context is embedded with the continuity rule."""
    prompt = build_prompt(
        {"AI/ML": [{"title": "T", "url": "u", "summary": "s", "source": "Blog", "id": "1"}]},
        SYNTH_CONFIG,
        previous_context="## 2026-06-13\n- [AI/ML] (HIGH) Some earlier theme: details",
    )
    assert "PREVIOUS DAYS" in prompt
    assert "Some earlier theme" in prompt
    assert "Continuity rule:" in prompt
    assert "never cite it in top_articles" in prompt


# --- category canonicalisation (AI/ML key fix) ----------------------------


@pytest.mark.parametrize("key", ["AI_ML", "ai/ml", "AI / ML", "ai-ml"])
def test_canonical_category_variants(key):
    """Sanitised category keys map back to the configured AI/ML spelling."""
    assert _canonical_category(key, ["AI/ML", "Technology"]) == "AI/ML"


def test_canonical_category_unknown_passthrough():
    """An unrecognised key is returned unchanged."""
    assert _canonical_category("Sports", ["AI/ML"]) == "Sports"


def test_synthesize_canonicalises_and_orders_ai_ml():
    """A response keyed AI_ML validates, canonicalises, and renders AI/ML first."""
    payload = {
        "AI_ML": VALID_SYNTHESIS["AI/ML"],
        "Technology": {"week_verdict": "v", "themes": []},
    }
    client = make_client([json.dumps(payload)])
    brief = synthesize(
        {"AI/ML": [{"title": "x", "url": "u", "summary": "s", "source": "Blog"}]},
        SYNTH_CONFIG,
        client=client,
    )
    assert "AI/ML" in brief.categories
    assert "AI_ML" not in brief.categories
    html_body = render_brief_html(
        brief, {}, "2026-06-14", 1, themed_order=["AI/ML", "Technology"]
    )
    assert ">AI/ML</h2>" in html_body
    assert html_body.index(">AI/ML</h2>") < html_body.index(">Technology</h2>")


# --- URL back-mapping -----------------------------------------------------


def test_match_title_exact():
    """Exact title match returns its URL."""
    mapping = {"Open model beats GPT": "https://x/a"}
    assert match_title_to_url("Open model beats GPT", mapping) == "https://x/a"


def test_match_title_normalised():
    """Case and whitespace differences still match."""
    mapping = {"Open Model  Beats GPT": "https://x/a"}
    assert match_title_to_url("open model beats gpt", mapping) == "https://x/a"


def test_match_title_word_overlap():
    """High word overlap matches when exact/normalised do not."""
    mapping = {"Open model beats GPT today": "https://x/a"}
    # 4/5 shared words = 0.8 Jaccard >= 0.75
    assert match_title_to_url("Open model beats GPT", mapping) == "https://x/a"


def test_match_title_unmatched():
    """Low overlap returns None (caller renders plain text)."""
    mapping = {"Completely different headline here": "https://x/a"}
    assert match_title_to_url("Open model beats GPT", mapping) is None


def test_best_title_match_resolves_bracket_prefixed_text():
    """A model that ignores the id instruction and echoes '[Source] Title' back
    still resolves to the plain candidate title via word-overlap, when the
    extra source words don't dilute the overlap below threshold."""
    candidates = ["OpenAI slows down training after its AI carried out hack"]
    text = "BBC] OpenAI slows down training after its AI carried out hack"
    assert _best_title_match(text, candidates) == candidates[0]


def test_best_title_match_gives_up_below_threshold():
    """A long/multi-word leaked prefix dilutes the overlap too far to match."""
    candidates = ["OpenAI slows down training after its AI carried out hack"]
    text = "BBC News - Technology] OpenAI slows down training after its AI carried out hack"
    assert _best_title_match(text, candidates) is None


def test_build_article_index():
    """Article index is id-keyed and includes articles without a URL too."""
    synthesis_input = {
        "AI/ML": [
            {"title": "A", "url": "https://x/a", "summary": "", "source": "Blog", "id": "1"},
            {"title": "B", "url": "", "summary": "", "source": "", "id": "2"},
        ]
    }
    index = build_article_index(synthesis_input)
    assert index == {
        "1": {"title": "A", "url": "https://x/a", "source": "Blog"},
        "2": {"title": "B", "url": "", "source": ""},
    }


def test_render_article_links_recovers_from_bracket_leak_and_drops_unmatched():
    """Regression test for the malformed-bracket bug: if Claude ignores the id
    instruction and echoes title-like text back (with a leaked source prefix,
    or the whole title wrapped in brackets), the renderer either composes a
    correct "[Source] Title" from a fuzzy title match, or drops the citation
    entirely - it never echoes Claude's malformed string into the email."""
    article_index = {
        "10": {
            "title": (
                "'Not a theoretical risk,' feds warn as attackers use AI-made "
                "code to hack critical infrastructure controllers"
            ),
            "url": "https://example.com/feds-warn",
            "source": "The Register",
        },
        "11": {
            "title": "OpenAI slows down training after its AI carried out hack",
            "url": "",
            "source": "BBC News - Technology",
        },
    }
    references = [
        # Whole title wrapped in brackets - clears the fuzzy-match threshold.
        "['Not a theoretical risk,' feds warn as attackers use AI-made code to "
        "hack critical infrastructure controllers]",
        # Multi-word source prefix missing its opening bracket - too different
        # from the plain title to clear the threshold, so it's dropped.
        "BBC News - Technology] OpenAI slows down training after its AI carried out hack",
    ]

    result = _render_article_links(references, article_index)

    # Never echoes Claude's malformed string verbatim.
    assert "['Not a theoretical risk,'" not in result
    assert "Technology] OpenAI" not in result

    # The whole-title-bracket case recovers via fuzzy match and is composed
    # correctly by Python, with its source prefix.
    assert "[The Register]" in result
    assert "feds warn as attackers use AI-made code" in result
    assert 'href="https://example.com/feds-warn"' in result

    # The source-prefix-leak case doesn't clear the fuzzy-match threshold and
    # is dropped rather than shown broken.
    assert "OpenAI slows down training" not in result


def test_render_article_links_drops_unresolvable_reference():
    """A reference matching no id and no title (even fuzzily) is dropped."""
    article_index = {"1": {"title": "Known article", "url": "https://x/a", "source": ""}}
    result = _render_article_links(["totally unrelated made-up headline"], article_index)
    assert result == ""


# --- inline citation leak into prose (_linkify_or_strip_citations) --------

# Distinct from VALID_ARTICLE_INDEX (whose entries deliberately have empty
# source/url) so link-text assertions below stay legible.
CITATION_ARTICLE_INDEX = {
    "1": {"title": "Open model beats GPT", "url": "https://x/a", "source": "Hacker News"},
    "5": {"title": "No URL story", "url": "", "source": "Blog"},
}


def test_linkify_citations_resolves_article_word_citation_to_link():
    """"(article N)" with a resolvable, URL-bearing id becomes a real link."""
    result = _linkify_or_strip_citations(
        "Adoption grew (article 1) this week.", CITATION_ARTICLE_INDEX
    )
    assert '<a href="https://x/a"' in result
    assert ">Hacker News</a>" in result
    assert "article 1" not in result


def test_linkify_citations_strips_unresolvable_article_word_citation():
    """An "(article N)" citation with an unknown id is dropped cleanly."""
    result = _linkify_or_strip_citations(
        "As noted (article 99) is not real.", CITATION_ARTICLE_INDEX
    )
    assert result == "As noted is not real."


def test_linkify_citations_resolves_bare_id_when_known():
    """A bare "(N)" is resolved the same way when N is a known id."""
    result = _linkify_or_strip_citations("See update (1) for details.", CITATION_ARTICLE_INDEX)
    assert '<a href="https://x/a"' in result
    assert ">Hacker News</a>" in result


def test_linkify_citations_leaves_unknown_bare_id_untouched():
    """A bare "(N)" that isn't a known id is left as ordinary prose (avoids
    false positives on legitimate parenthetical numbers)."""
    result = _linkify_or_strip_citations("Revenue rose (20) percent.", CITATION_ARTICLE_INDEX)
    assert result == "Revenue rose (20) percent."


def test_linkify_citations_drops_resolved_id_with_no_url():
    """A citation that resolves but has no URL is stripped, not linked -
    matching _render_article_links' no-URL-still-known behaviour."""
    result = _linkify_or_strip_citations(
        "Related coverage (article 5) continues.", CITATION_ARTICLE_INDEX
    )
    assert result == "Related coverage continues."


def test_linkify_citations_preserves_html_escaping_around_a_citation():
    """Plain-text portions around a resolved citation are still escaped."""
    result = _linkify_or_strip_citations(
        "Growth < 5% (article 1) & rising", CITATION_ARTICLE_INDEX
    )
    assert "&lt; 5%" in result
    assert "&amp; rising" in result
    assert '<a href="https://x/a"' in result


def test_linkify_citations_no_citation_present():
    """No parenthetical citation -> identical to a plain html.escape()."""
    text = "Plain prose with < and & but no parens."
    assert _linkify_or_strip_citations(text, CITATION_ARTICLE_INDEX) == html.escape(text)


# --- synthesize -----------------------------------------------------------


def test_synthesize_valid():
    """A valid JSON response parses into a BriefSynthesis."""
    client = make_client([json.dumps(VALID_SYNTHESIS)])
    brief = synthesize(
        {"AI/ML": [{"title": "x", "url": "u", "summary": "s"}]},
        SYNTH_CONFIG,
        client=client,
    )
    assert isinstance(brief, BriefSynthesis)
    assert brief.categories["AI/ML"].themes[0].signal_strength == "HIGH"
    assert brief.categories["AI/ML"].themes[1].relevance_to_reader is None
    assert brief.personal.summary == "Cycling season heats up."
    assert client.messages.stream.call_count == 1


def test_synthesize_strips_json_fences():
    """A fenced ```json response is parsed after stripping fences."""
    fenced = f"```json\n{json.dumps(VALID_SYNTHESIS)}\n```"
    client = make_client([fenced])
    brief = synthesize(
        {"AI/ML": [{"title": "x", "url": "u", "summary": "s"}]},
        SYNTH_CONFIG,
        client=client,
    )
    assert isinstance(brief, BriefSynthesis)
    assert "AI/ML" in brief.categories


def test_synthesize_retries_then_skips():
    """Garbage on both attempts yields None and a single retry."""
    client = make_client(["not json at all", "still not json"])
    brief = synthesize(
        {"AI/ML": [{"title": "x", "url": "u", "summary": "s"}]},
        SYNTH_CONFIG,
        client=client,
    )
    assert brief is None
    assert client.messages.stream.call_count == 2


def test_synthesize_empty_input_skips():
    """No themed articles means no API call and a None result."""
    client = make_client([json.dumps(VALID_SYNTHESIS)])
    assert synthesize({}, SYNTH_CONFIG, client=client) is None
    assert client.messages.stream.call_count == 0


# --- schema validation ----------------------------------------------------


def test_brieftheme_rejects_bad_signal():
    """An invalid signal_strength fails validation."""
    with pytest.raises(pydantic.ValidationError):
        BriefTheme(theme="t", signal_strength="URGENT")


def test_brieftheme_preserves_null_relevance():
    """relevance_to_reader defaults to None and is preserved."""
    theme = BriefTheme(theme="t", signal_strength="HIGH")
    assert theme.relevance_to_reader is None


# --- signal badge ---------------------------------------------------------


@pytest.mark.parametrize(
    "signal,expected_hex",
    [("HIGH", "#f44336"), ("STRATEGIC", "#ff9800"), ("GENERAL", "#4caf50")],
)
def test_signal_badge_colours(signal, expected_hex):
    """Each signal renders with its expected border colour and label."""
    badge = _signal_badge(signal)
    assert expected_hex in badge
    assert signal in badge
    assert badge.startswith("<span")


# --- rendering ------------------------------------------------------------


@pytest.fixture
def rendered_html():
    """Render a brief from VALID_SYNTHESIS with a known article index."""
    brief = BriefSynthesis(
        categories={"AI/ML": VALID_SYNTHESIS["AI/ML"]},
        cross_cutting=VALID_SYNTHESIS["cross_cutting"],
        personal=VALID_SYNTHESIS["personal"],
    )
    return render_brief_html(
        brief, VALID_ARTICLE_INDEX, "2026-06-14", 7, themed_order=["AI/ML"]
    )


def test_render_contains_core_sections(rendered_html):
    """Header, cross-cutting, and personal sections are present."""
    assert "RSS Brief — 2026-06-14" in rendered_html
    assert "Cross-Cutting Signals" in rendered_html
    assert "Personal" in rendered_html
    assert "Why this matters to you:" in rendered_html


def test_render_links_matched_articles(rendered_html):
    """Matched titles render as links; unmatched render as plain text."""
    assert '<a href="https://x/a"' in rendered_html
    assert "Open model beats GPT</a>" in rendered_html
    # "Benchmark tweak" has no URL in the map -> no anchor for it
    assert '">Benchmark tweak</a>' not in rendered_html
    assert "Benchmark tweak" in rendered_html


def test_render_relevance_omitted_when_null(rendered_html):
    """The GENERAL theme has null relevance and renders no relevance line."""
    # Only one "Why this matters" line (for the HIGH theme), not two.
    assert rendered_html.count("Why this matters to you:") == 1


def test_render_is_email_safe(rendered_html):
    """No JavaScript, flexbox, grid, or inline scripts in the output."""
    lowered = rendered_html.lower()
    assert "<script" not in lowered
    assert "display: flex" not in lowered
    assert "display: grid" not in lowered


def test_render_resolves_inline_citations_leaked_into_prose():
    """End-to-end: if Claude ignores PROMPT_TEMPLATE and writes an id citation
    inline into tldr/relevance_to_reader/week_verdict/implication/summary, the
    rendered email resolves it into a real link (or drops it) - it never
    reaches the reader as a bare "(article N)"."""
    synthesis = {
        "AI/ML": {
            "week_verdict": "A big week (article 1) for open models.",
            "themes": [
                {
                    "theme": "Open-weight models close the gap",
                    "signal_strength": "HIGH",
                    "tldr": "Open models now rival proprietary ones (article 1).",
                    "top_articles": ["1"],
                    "relevance_to_reader": "Affects your tooling choices (1).",
                },
            ],
        },
        "cross_cutting": [
            {
                "signal": "AI reshapes security tooling",
                "categories_involved": ["AI/ML"],
                "implication": "Watch for AI-driven SOC tools (article 1).",
            }
        ],
        "personal": {
            "top_stories": ["4"],
            "summary": "Cycling season heats up (article 4).",
        },
    }
    brief = BriefSynthesis(
        categories={"AI/ML": synthesis["AI/ML"]},
        cross_cutting=synthesis["cross_cutting"],
        personal=synthesis["personal"],
    )
    html_body = render_brief_html(
        brief, VALID_ARTICLE_INDEX, "2026-06-14", 7, themed_order=["AI/ML"]
    )
    assert "(article" not in html_body
    # Resolved citations become real links to the cited article's URL.
    assert html_body.count('href="https://x/a"') >= 3  # tldr, relevance, implication
    assert 'href="https://x/cycling"' in html_body  # personal summary


# --- generate_brief orchestration -----------------------------------------


def test_generate_brief_disabled(monkeypatch):
    """BRIEF_ENABLED=false skips the brief entirely."""
    monkeypatch.setenv("BRIEF_ENABLED", "false")
    assert generate_brief({}, date="2026-06-14", article_count=0) is None


def test_generate_brief_end_to_end(monkeypatch):
    """Full path: categories -> synthesis -> rendered HTML."""
    monkeypatch.setenv("BRIEF_ENABLED", "true")
    categories = {
        "AI/ML": [
            {"title": "Open model beats GPT", "link": "https://x/a", "summary": "s"},
            {"title": "Llama 4 released", "link": "https://x/b", "summary": "s"},
        ],
        "Cycling": [
            {
                "title": "Tour de France route announced",
                "link": "https://x/cycling",
                "summary": "s",
            }
        ],
        "Science": [{"title": "Excluded", "link": "https://x/s", "summary": "s"}],
    }
    client = make_client([json.dumps(VALID_SYNTHESIS)])
    html_body = generate_brief(
        categories, date="2026-06-14", article_count=4, client=client
    )
    assert html_body is not None
    assert "RSS Brief — 2026-06-14" in html_body
    assert '<a href="https://x/a"' in html_body
    assert "Excluded" not in html_body


def test_generate_brief_full_returns_synthesis_and_index(monkeypatch):
    """generate_brief_full exposes the validated synthesis + article index too."""
    monkeypatch.setenv("BRIEF_ENABLED", "true")
    categories = {
        "AI/ML": [
            {"title": "Open model beats GPT", "link": "https://x/a", "summary": "s"},
            {"title": "Llama 4 released", "link": "https://x/b", "summary": "s"},
        ],
        "Cycling": [
            {
                "title": "Tour de France route announced",
                "link": "https://x/cycling",
                "summary": "s",
            }
        ],
    }
    client = make_client([json.dumps(VALID_SYNTHESIS)])
    result = generate_brief_full(
        categories, date="2026-06-14", article_count=3, client=client
    )
    assert result is not None
    assert "RSS Brief — 2026-06-14" in result.html
    assert isinstance(result.synthesis, BriefSynthesis)
    assert "AI/ML" in result.synthesis.categories
    assert result.article_index["1"]["title"] == "Open model beats GPT"


def test_generate_brief_skips_when_no_themed(monkeypatch):
    """Only excluded categories present -> no brief."""
    monkeypatch.setenv("BRIEF_ENABLED", "true")
    categories = {"Science": [{"title": "S", "link": "https://x/s", "summary": "s"}]}
    client = make_client([json.dumps(VALID_SYNTHESIS)])
    assert (
        generate_brief(categories, date="2026-06-14", article_count=1, client=client)
        is None
    )


# --- integration with the Lambda handler ----------------------------------


@pytest.fixture
def handler_env(monkeypatch):
    """Environment variables required by the email Lambda handler."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")
    monkeypatch.setenv("LAST_RUN_PARAMETER", "test-lastrun")
    monkeypatch.setenv("SOURCE_EMAIL_ADDRESS", "source@example.com")
    monkeypatch.setenv("TO_EMAIL_ADDRESS", "to@example.com")
    monkeypatch.setenv("RSS_BUCKET", "test-bucket")


def _batch_event():
    """A minimal Step Functions event for the handler."""
    return {"batch_id": "batch-123", "request_counts": {"succeeded": 1, "errored": 0}}


def _setup_handler_mocks(mock_boto3_client, mock_anthropic):
    """Wire SSM + a single succeeded batch result with two themed categories."""
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    response = {
        "categories": {
            "Technology": [{"title": "T1", "link": "https://x/1", "summary": "s"}],
            "AI/ML": [{"title": "A1", "link": "https://x/2", "summary": "s"}],
        }
    }
    result = MagicMock()
    result.result.type = "succeeded"
    result.result.message.content = [MagicMock(text=json.dumps(response))]
    result.custom_id = "email-batch-0"
    mock_anthropic.return_value.messages.batches.results.return_value = [result]


@patch("rss_email.retrieve_and_send_email.generate_brief_full")
@patch("rss_email.retrieve_and_send_email.set_last_run")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.create_html")
@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_handler_sends_brief_after_digest(
    mock_boto3_client,
    mock_anthropic,
    mock_create_html,
    mock_send_via_ses,
    mock_set_last_run,
    mock_generate_brief_full,
    handler_env,
):
    """The handler sends a second (brief) email after the digest."""
    _setup_handler_mocks(mock_boto3_client, mock_anthropic)
    mock_create_html.return_value = "<digest/>"
    mock_generate_brief_full.return_value = BriefResult(
        html="<brief/>", synthesis=BriefSynthesis(), article_index={}
    )

    result = lambda_handler(_batch_event(), None)

    assert result["status"] == "success"
    assert mock_send_via_ses.call_count == 2
    subjects = [call.args[2] for call in mock_send_via_ses.call_args_list]
    assert any(subject.startswith("RSS Brief —") for subject in subjects)
    assert "Your Daily RSS Digest" in subjects


@patch("rss_email.retrieve_and_send_email.generate_brief_full")
@patch("rss_email.retrieve_and_send_email.set_last_run")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.create_html")
@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_handler_brief_failure_does_not_block_digest(
    mock_boto3_client,
    mock_anthropic,
    mock_create_html,
    mock_send_via_ses,
    mock_set_last_run,
    mock_generate_brief_full,
    handler_env,
):
    """A brief failure leaves the digest sent and last_run advanced."""
    _setup_handler_mocks(mock_boto3_client, mock_anthropic)
    mock_create_html.return_value = "<digest/>"
    mock_generate_brief_full.side_effect = RuntimeError("synthesis boom")

    result = lambda_handler(_batch_event(), None)

    assert result["status"] == "success"
    mock_send_via_ses.assert_called_once_with(
        "to@example.com", "source@example.com", "Your Daily RSS Digest", "<digest/>"
    )
    mock_set_last_run.assert_called_once_with("test-lastrun")


# --- CLI dry-run ----------------------------------------------------------


def test_cli_dry_run_writes_file(tmp_path):
    """--dry-run writes the rendered HTML to a file instead of sending."""
    output_file = tmp_path / "brief_output.html"
    categorized = MagicMock()
    categorized.categories = {
        "AI/ML": [{"title": "A1", "link": "https://x/2", "summary": "s"}]
    }

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch.object(cli_brief_generator, "read_s3_file", return_value="<rss/>")
        )
        stack.enter_context(
            patch.object(
                cli_brief_generator, "filter_items", return_value=[{"title": "A1"}]
            )
        )
        stack.enter_context(
            patch.object(
                cli_brief_generator,
                "process_articles_with_claude",
                return_value=categorized,
            )
        )
        stack.enter_context(
            patch.object(
                cli_brief_generator, "generate_brief", return_value="<brief-html/>"
            )
        )
        mock_send = stack.enter_context(
            patch.object(cli_brief_generator, "send_via_ses")
        )
        cli_brief_generator.generate(
            "bucket",
            "key",
            datetime(2026, 6, 14),
            dry_run=True,
            output_file=str(output_file),
            debug=False,
        )

    assert output_file.read_text(encoding="utf-8") == "<brief-html/>"
    mock_send.assert_not_called()
