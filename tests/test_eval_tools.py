"""Tests for the eval helpers (eval/provider.py, summarise.py, make_fixture.py)."""
# pylint: disable=missing-function-docstring,wrong-import-position,import-error

import json
import logging
import os
import sys
from unittest.mock import patch

import pytest

EVAL_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "eval")
sys.path.insert(0, EVAL_DIR)

import make_fixture  # noqa: E402
import provider  # noqa: E402
import summarise  # noqa: E402


def test_apply_overrides_only_allows_known_keys():
    base = {"model": "a", "effort": "high", "reader_profile": "keep"}
    merged = provider.apply_overrides(base, {"model": "b", "effort": "low", "reader_profile": "x"})
    assert merged == {"model": "b", "effort": "low", "reader_profile": "keep"}
    assert base["model"] == "a"


def test_cost_usd_uses_prices_file():
    assert provider.cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert provider.cost_usd("claude-sonnet-5-5", 10_000, 20_000) == pytest.approx(0.22)
    assert provider.cost_usd("no-such-model", 1, 1) is None


@pytest.mark.parametrize("path, expected", [
    ("fixtures/2026-06-15/digest_articles.json", "2026-06-15"),
    ("fixtures/2026-06-15-lowrank/digest_articles.json", "2026-06-15"),
])
def test_fixture_date(path, expected):
    assert provider.fixture_date(path) == expected


def test_call_api_overrides_config_and_reports_usage(tmp_path):
    fixture = tmp_path / "2026-06-15" / "digest_articles.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({"AI/ML": [{"title": "t", "url": "u", "summary": "s", "source": "x"}]}))

    class Brief:
        """Stand-in for BriefSynthesis."""

        @staticmethod
        def model_dump_json():
            return '{"categories": {}}'

    seen = {}

    def fake_synthesize(_input, config, date=None):
        seen.update(config=config, date=date)
        logging.getLogger("rss_email.usage_log").info(
            "CLAUDE_USAGE step=%s model=%s batch=%s requests=%d "
            "input=%d output=%d cache_write=%d cache_read=%d stop=%s",
            "brief", config["model"], False, 1, 1000, 2000, 0, 0, "end_turn",
        )
        return Brief()

    with patch.object(provider, "synthesize", fake_synthesize):
        result = provider.call_api(
            "", {"config": {"model": "claude-sonnet-5-5", "effort": "medium"}},
            {"vars": {"fixture": str(fixture)}},
        )

    assert seen["config"]["model"] == "claude-sonnet-5-5"
    assert seen["config"]["effort"] == "medium"
    assert seen["date"] == "2026-06-15"
    assert result["tokenUsage"] == {"prompt": 1000, "completion": 2000, "total": 3000}
    assert result["cost"] == pytest.approx((1000 * 2 + 2000 * 10) / 1e6)


def test_call_api_reports_synthesis_failure(tmp_path):
    fixture = tmp_path / "2026-06-15" / "digest_articles.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({"AI/ML": [{"title": "t", "url": "u", "summary": "s", "source": "x"}]}))
    with patch.object(provider, "synthesize", return_value=None):
        result = provider.call_api("", {}, {"vars": {"fixture": str(fixture)}})
    assert "error" in result and "output" not in result


def _row(label, success, cost, completion, assertion_passes):
    return {
        "provider": {"label": label},
        "success": success,
        "cost": cost,
        "response": {"tokenUsage": {"completion": completion}},
        "gradingResult": {"componentResults": [
            {"pass": ok, "assertion": {"type": "javascript", "value": f"file://assertions/{name}"}}
            for name, ok in assertion_passes.items()
        ]},
    }


def test_summarise_flags_variant_with_any_failed_run():
    rows = summarise.summarise([
        _row("base", True, 0.40, 20000, {"major_coverage.cjs": True}),
        _row("base", True, 0.44, 22000, {"major_coverage.cjs": True}),
        _row("cheap", True, 0.10, 8000, {"major_coverage.cjs": True}),
        _row("cheap", False, 0.12, 9000, {"major_coverage.cjs": False}),
    ], baseline="base")
    base, cheap = rows
    assert base["adoptable"] and base["baseline"]
    assert not cheap["adoptable"]
    assert cheap["assertions"]["major_coverage.cjs"] == 0.5
    assert cheap["cost_vs_baseline"] == pytest.approx(0.11 / 0.42)
    assert "NOT SAFE" in summarise.render(rows)


def test_summarise_empty():
    assert not summarise.summarise([])


def test_digest_to_text_matches_grader_format():
    digest = {"AI/ML": [{"title": "T", "source": "S", "summary": "Sum"}]}
    assert make_fixture.digest_to_text(digest) == "## AI/ML\n- [S] T\n  Sum\n"


def test_relabel_to_low_rank_changes_only_must_cover_sources():
    digest = {"Cat": [
        {"title": "Major story", "source": "Blog", "summary": "a"},
        {"title": "Minor", "source": "Blog", "summary": "b"},
    ]}
    out = make_fixture.relabel_to_low_rank(digest, ["major story"])
    assert out["Cat"][0]["source"] in make_fixture.LOW_RANK_SOURCES
    assert out["Cat"][1]["source"] == "Blog"
    assert digest["Cat"][0]["source"] == "Blog"


def test_lowrank_fixture_in_repo_is_consistent():
    base = os.path.join(EVAL_DIR, "fixtures")
    with open(os.path.join(base, "2026-06-15", "digest_articles.json"), encoding="utf-8") as handle:
        src = json.load(handle)
    with open(os.path.join(base, "2026-06-15-lowrank", "digest_articles.json"), encoding="utf-8") as handle:
        dst = json.load(handle)
    assert {k: [a["title"] for a in v] for k, v in src.items()} == \
           {k: [a["title"] for a in v] for k, v in dst.items()}
