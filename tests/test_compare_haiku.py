"""Tests for eval/compare_haiku.py (metrics and the run/report flow with a fake client)."""
# pylint: disable=missing-function-docstring,wrong-import-position,import-error

import json
import os
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "eval"))

import compare_haiku as ch  # noqa: E402


def test_pair_set_and_grouping_agreement():
    base = [[0, 1, 2], [3]]
    same = ch.grouping_agreement(base, [[0, 1, 2], [3]])
    assert same["precision"] == 1 and same["recall"] == 1
    split = ch.grouping_agreement(base, [[0, 1], [2], [3]])
    assert split["precision"] == 1 and split["recall"] == pytest.approx(1 / 3)
    merged = ch.grouping_agreement(base, [[0, 1, 2, 3]])
    assert merged["recall"] == 1 and merged["precision"] == pytest.approx(0.5)
    assert ch.grouping_agreement([[0], [1]], [[0], [1]])["recall"] is None


def test_category_agreement_counts_missing_as_disagreement():
    base = {"g0": "AI/ML", "g1": "Business", "g2": "Cycling", "g3": "Politics"}
    agree, diffs = ch.category_agreement(base, {"g0": "AI/ML", "g1": "Technology", "g2": "Cycling"})
    assert agree == pytest.approx(0.5)
    assert ("g3", "Politics", "<missing>") in diffs and ("g1", "Business", "Technology") in diffs
    assert ch.category_agreement({}, {"g0": "x"}) == (None, [])


def test_variant_parse_and_params():
    assert ch.Variant.parse("claude-haiku-5-5:low").params() == {"output_config": {"effort": "low"}}
    assert not ch.Variant.parse("claude-haiku-4-5").params()
    assert ch.Variant.parse("claude-haiku-5-5:low").label == "claude-haiku-5-5:low"


def test_verdict_reports_each_missed_bar():
    run = ch.Run(ch.Variant("claude-haiku-5-5"), groups=[[0]], refusals=1, max_token_stops=2)
    reasons = ch.verdict(run, 0.8, 0.5)
    text = " ".join(reasons)
    assert "refusal" in text and "max_tokens" in text
    assert "category agreement 80%" in text and "grouping recall 50%" in text
    clean = ch.Run(ch.Variant("claude-haiku-5-5"), groups=[[0]])
    assert not ch.verdict(clean, 0.95, 0.9)


class FakeClient:
    """Answers grouping and categorising prompts; the candidate disagrees on one group."""

    def __init__(self, thinking_first=False, refuse=False):
        self.messages = self
        self.thinking_first = thinking_first
        self.refuse = refuse
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if self.refuse:
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=0))
        if "clustering" in prompt:
            body = {"groups": [["article_0", "article_1"], ["article_2"]], "article_count": 3}
        else:
            gids = re.findall(r'"group_id": "(group_\d+)"', prompt)
            cats = {}
            for gid in gids:
                cat = "Business" if (gid == "group_1" and kwargs.get("output_config")) else "AI/ML"
                cats.setdefault(cat, []).append(
                    {"group_id": gid, "title": gid, "summary": "two words", "category": cat})
            body = {"categories": cats}
        content = [SimpleNamespace(type="text", text=json.dumps(body))]
        if self.thinking_first:
            content.insert(0, SimpleNamespace(type="thinking", thinking="hmm"))
        return SimpleNamespace(content=content, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=500))


ARTICLES = [{"title": f"T{i}", "description": "d", "link": f"https://x/{i}", "pubDate": "now",
             "sourceName": "Src"} for i in range(3)]
STORED_GROUPS = [[0, 1], [2]]


def _run(variant, client):
    run = ch.Run(ch.Variant.parse(variant))
    ch.run_grouping(client, run, ARTICLES)
    ch.run_categorise(client, run, ARTICLES, STORED_GROUPS)
    return run


def test_run_handles_thinking_first_responses_and_sends_effort():
    client = FakeClient(thinking_first=True)
    run = _run("claude-haiku-5-5:low", client)
    assert run.groups == [[0, 1], [2]]
    assert set(run.categories) == {"group_0", "group_1"}
    assert run.calls == 2 and run.input_tokens == 2000 and run.parse_failures == 0
    assert all(c["output_config"] == {"effort": "low"} for c in client.calls)


def test_run_records_refusals():
    run = _run("claude-haiku-5-5:low", FakeClient(refuse=True))
    assert run.refusals == 2 and run.groups is None and not run.categories


def test_report_marks_disagreement_and_pass_bar():
    base = _run("claude-haiku-4-5", FakeClient())
    cand = _run("claude-haiku-5-5:low", FakeClient())
    text, summary = ch.report([base, cand], base, STORED_GROUPS, {"group_1": "T2"})
    assert summary["claude-haiku-5-5:low"]["category_agreement"] == pytest.approx(0.5)
    assert "FAIL" in text and "group_1: AI/ML -> Business" in text
    assert summary["claude-haiku-4-5"]["failures"] == []


def test_to_fixture_shape_matches_brief_eval():
    run = _run("claude-haiku-4-5", FakeClient())
    digest = ch.to_fixture(ARTICLES, STORED_GROUPS, run)
    assert set(digest["AI/ML"][0]) == {"title", "url", "summary", "source"}
    assert [a["title"] for a in digest["AI/ML"]] == ["T0", "T2"]
