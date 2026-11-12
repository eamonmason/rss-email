"""Tests for eval/compare_haiku.py (metrics, decision rule, adjudication, fake-client runs)."""
# pylint: disable=missing-function-docstring,wrong-import-position,import-error

import json
import os
import random
import re
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "eval"))

import compare_haiku as ch  # noqa: E402

VISIBLE = {"AI/ML", "Technology", "Business", "Cycling"}


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


def test_visible_agreement_ignores_flips_between_invisible_categories():
    base = {"g0": "AI/ML", "g1": "Other", "g2": "Science", "g3": "Business", "g4": "Other"}
    cand = {"g0": "AI/ML", "g1": "Environment", "g2": "Business", "g3": "Technology", "g4": "Other"}
    agree, flips = ch.visible_agreement(base, cand, VISIBLE)
    # visible groups: g0 (same), g2 (Science->Business, flips in), g3 (Business->Technology)
    assert agree == pytest.approx(1 / 3) and flips == 1
    assert ch.visible_agreement({"g0": "Other"}, {"g0": "Science"}, VISIBLE) == (None, 0)


def test_variant_parse_params_and_tag():
    low = ch.Variant.parse("claude-haiku-5-5:low")
    assert low.params() == {"output_config": {"effort": "low"}} and low.label == "claude-haiku-5-5:low"
    assert not ch.Variant.parse("claude-haiku-4-5").params()
    second = ch.Variant.parse("claude-haiku-4-5#2")
    assert second.model == "claude-haiku-4-5" and second.tag == "2" and second.label.endswith("#2")


@pytest.mark.parametrize("text, expected", [
    ('{"groups": [["article_0", "article_1"], ["article_2"]]}', None),
    ("not json at all", "unparseable reply"),
    ('{"groupings": []}', "no 'groups' list in reply"),
    ('{"groups": [["0", "1"], ["2"]]}', "reply places only 0% of articles"),
    ('{"groups": [["article_0"]]}', "reply places only 33% of articles"),
])
def test_grouping_fallback_reason(text, expected):
    assert ch.grouping_fallback_reason(text, 3) == expected


def test_win_rate():
    assert ch.win_rate({"x": 3, "y": 1, "tie": 2}) == pytest.approx(4 / 6)
    assert ch.win_rate({"x": 0, "y": 0, "tie": 0, "invalid": 4}) is None


def _metrics(**overrides):
    base = {"cat_all": 0.80, "noise_all": 0.82, "cat_vis": 0.85, "noise_vis": 0.86,
            "recall": 0.90, "noise_recall": 0.95, "win_rate": 0.55}
    base.update(overrides)
    return base


def test_verdict_is_relative_to_the_noise_floor():
    run = ch.Run(ch.Variant("claude-haiku-5-5"), groups=[[0]])
    assert not ch.verdict(run, _metrics())  # 80% is within 5 points of the 82% noise floor
    reasons = " ".join(ch.verdict(run, _metrics(cat_all=0.70, recall=0.80, win_rate=0.30)))
    assert "category agreement 70% < 77%" in reasons
    assert "grouping recall 80% < 85%" in reasons
    assert "adjudication win rate 30% < 45%" in reasons


def test_verdict_uses_fixed_bars_without_a_noise_run():
    run = ch.Run(ch.Variant("claude-haiku-5-5"), groups=[[0]])
    none = _metrics(noise_all=None, noise_vis=None, noise_recall=None)
    assert any("90%" in r for r in ch.verdict(run, {**none, "cat_all": 0.85}))


def test_verdict_reports_run_failures_and_grouping_fallback():
    run = ch.Run(ch.Variant("claude-haiku-5-5"), groups=[[0]], refusals=1, max_token_stops=2,
                 missing_groups=3, grouping_fallback="unparseable reply")
    text = " ".join(ch.verdict(run, _metrics()))
    assert "refusal" in text and "max_tokens" in text and "3 group(s) missing" in text
    assert "fell back to singletons (unparseable reply)" in text


ARTICLES = [{"title": f"T{i}", "description": "d", "link": f"https://x/{i}", "pubDate": "now",
             "sourceName": "Src"} for i in range(3)]
STORED_GROUPS = [[0, 1], [2]]


class FakeClient:
    """Answers grouping, categorising and adjudication prompts."""

    def __init__(self, thinking_first=False, refuse=False, grouping_reply=None, flip_group_1=False):
        self.messages = self
        self.thinking_first = thinking_first
        self.refuse = refuse
        self.grouping_reply = grouping_reply
        self.flip_group_1 = flip_group_1
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
        if self.refuse:
            return SimpleNamespace(content=[], stop_reason="refusal",
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=0))
        if kwargs["model"] == ch.ADJUDICATOR:
            items = json.loads(prompt.split("Articles:\n", 1)[1])
            choices = []
            for item in items:
                pick = "A" if item["option_a"] == "Truth" else "B" if item["option_b"] == "Truth" else "TIE"
                choices.append({"id": item["id"], "choice": pick})
            body = {"choices": choices}
            text = json.dumps(body)
        elif "clustering" in prompt:
            text = self.grouping_reply or json.dumps(
                {"groups": [["article_0", "article_1"], ["article_2"]], "article_count": 3})
        else:
            cats = {}
            for gid in re.findall(r'"group_id": "(group_\d+)"', prompt):
                cat = "Business" if (self.flip_group_1 and gid == "group_1") else "AI/ML"
                cats.setdefault(cat, []).append(
                    {"group_id": gid, "title": gid, "summary": "two words", "category": cat})
            text = json.dumps({"categories": cats})
        content = [SimpleNamespace(type="text", text=text)]
        if self.thinking_first:
            content.insert(0, SimpleNamespace(type="thinking", thinking="hmm"))
        return SimpleNamespace(content=content, stop_reason="end_turn", usage=usage)


def _run(variant, client):
    run = ch.Run(ch.Variant.parse(variant))
    ch.run_grouping(client, run, ARTICLES)
    ch.run_categorise(client, run, ARTICLES, STORED_GROUPS)
    return run


def test_run_handles_thinking_first_responses_and_sends_effort():
    client = FakeClient(thinking_first=True)
    run = _run("claude-haiku-5-5:low", client)
    assert run.groups == [[0, 1], [2]] and run.grouping_fallback is None
    assert run.nonsingleton_groups == 1
    assert set(run.categories) == {"group_0", "group_1"}
    assert run.calls == 2 and run.input_tokens == 2000 and run.parse_failures == 0
    assert run.stage_tokens["grouping"] == [1000, 500]
    assert all(c["output_config"] == {"effort": "low"} for c in client.calls)


def test_run_records_refusals():
    run = _run("claude-haiku-5-5:low", FakeClient(refuse=True))
    assert run.refusals == 2 and run.groups is None and not run.categories
    assert run.grouping_fallback == "no usable reply"


def test_silent_singleton_fallback_is_flagged_and_saved_raw(tmp_path):
    run = _run("claude-haiku-5-5:low", FakeClient(grouping_reply="Sorry, here you go: nonsense"))
    assert run.groups == [[0], [1], [2]]  # what production would silently have used
    assert run.grouping_fallback == "unparseable reply" and run.nonsingleton_groups == 0
    ch.write_raw([run], str(tmp_path))
    assert (tmp_path / "haiku-5-5-low-grouping-0.txt").read_text() == "Sorry, here you go: nonsense"


def test_prompt_hides_model_names_and_adjudication_maps_sides_back():
    pairs = [("group_0", "Truth", "Wrong"), ("group_1", "Wrong", "Truth"), ("group_2", "Wrong", "Other")]
    client = FakeClient()
    usage = [0, 0]
    counts = ch.adjudicate(client, pairs, ARTICLES, [[0], [1], [2]], random.Random(3), usage)
    # Truth is category_x in the first pair and category_y in the second, wherever it was shown.
    assert counts == {"x": 1, "y": 1, "tie": 1, "invalid": 0}
    assert usage == [1000, 500]
    prompt = client.calls[0]["messages"][0]["content"]
    assert "haiku" not in prompt.lower() and "claude" not in prompt.lower()
    assert client.calls[0]["model"] == ch.ADJUDICATOR


def test_adjudication_order_is_randomised():
    pairs = [(f"group_{i % 2}", "Truth", "Wrong") for i in range(40)]
    client = FakeClient()
    ch.adjudicate(client, pairs, ARTICLES, STORED_GROUPS, random.Random(1))
    items = json.loads(client.calls[0]["messages"][0]["content"].split("Articles:\n", 1)[1])
    shown_first = {item["option_a"] for item in items}
    assert shown_first == {"Truth", "Wrong"}


def test_evaluate_measures_noise_floor_and_qualifies_by_it():
    base = _run("claude-haiku-4-5", FakeClient())
    noise = _run("claude-haiku-4-5#2", FakeClient(flip_group_1=True))  # 4.5 disagrees with itself
    good = _run("claude-haiku-5-5:low", FakeClient(flip_group_1=True))  # as noisy as 4.5 is
    results = ch.evaluate([base, noise, good], STORED_GROUPS, VISIBLE, {})
    assert results[noise.variant.label]["reference"] and results[base.variant.label]["reference"]
    metrics = results[good.variant.label]["metrics"]
    assert metrics["noise_all"] == pytest.approx(0.5) == metrics["cat_all"]
    assert metrics["noise_recall"] == 1.0 and metrics["recall"] == 1.0
    assert results[good.variant.label]["failures"] == []


def test_evaluate_fails_a_candidate_that_is_worse_than_the_noise_floor():
    base = _run("claude-haiku-4-5", FakeClient())
    noise = _run("claude-haiku-4-5#2", FakeClient())  # 4.5 agrees with itself fully
    bad = _run("claude-haiku-5-5:low", FakeClient(flip_group_1=True))
    results = ch.evaluate([base, noise, bad], STORED_GROUPS, VISIBLE, {bad.variant.label: {
        "x": 1, "y": 4, "tie": 0, "invalid": 0}})
    failures = " ".join(results[bad.variant.label]["failures"])
    assert "category agreement 50% < 95%" in failures and "win rate 20% < 45%" in failures


def test_report_lists_visible_disagreements_and_verdict():
    base = _run("claude-haiku-4-5", FakeClient())
    noise = _run("claude-haiku-4-5#2", FakeClient())
    bad = _run("claude-haiku-5-5:low", FakeClient(flip_group_1=True))
    results = ch.evaluate([base, noise, bad], STORED_GROUPS, VISIBLE, {})
    text = ch.report([base, noise, bad], results, VISIBLE, {"group_1": "T2"})
    assert "DOES NOT QUALIFY" in text and "group_1: AI/ML -> Business" in text
    assert "Noise floor (Haiku 4.5 vs itself)" in text


def test_to_fixture_shape_matches_brief_eval():
    run = _run("claude-haiku-4-5", FakeClient())
    digest = ch.to_fixture(ARTICLES, STORED_GROUPS, run)
    assert set(digest["AI/ML"][0]) == {"title", "url", "summary", "source"}
    assert [a["title"] for a in digest["AI/ML"]] == ["T0", "T2"]


def test_a_non_batch_metadata_file_gets_a_clear_error(tmp_path, capsys):
    report = tmp_path / "batch-1.report.json"
    report.write_text(json.dumps({"claude-haiku-4-5": {"calls": 1}}))
    with pytest.raises(SystemExit):
        ch.main([str(report)])
    assert "not a batch-metadata file" in capsys.readouterr().err


def test_evaluate_works_with_a_single_baseline_variant():
    base = _run("claude-haiku-4-5", FakeClient())
    results = ch.evaluate([base], STORED_GROUPS, VISIBLE, {})
    assert results[base.variant.label]["reference"]
    assert results[base.variant.label]["metrics"]["noise_all"] is None
    assert "reference run" in ch.report([base], results, VISIBLE, {})


def test_to_fixture_can_match_the_production_brief_input():
    run = _run("claude-haiku-4-5", FakeClient())
    run.categories["group_1"] = "Other"  # a category the brief never sees
    digest = ch.to_fixture(ARTICLES, STORED_GROUPS, run, (["AI/ML", "Business"], ["Cycling"]))
    assert list(digest) == ["AI/ML"] and [a["title"] for a in digest["AI/ML"]] == ["T0"]
