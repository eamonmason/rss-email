"""Tests for the eval helpers (eval/provider.py, summarise.py, make_fixture.py)."""
# pylint: disable=missing-function-docstring,wrong-import-position,import-error

import json
import logging
import os
import re
import shutil
import subprocess
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

    def fake_synthesize(_input, config, previous_context="", date=None):
        seen.update(config=config, date=date, previous_context=previous_context)
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


def test_summarise_flags_variant_that_trails_the_baseline():
    rows = summarise.summarise([
        _row("base", True, 0.40, 20000, {"major_coverage.cjs": True}),
        _row("base", True, 0.44, 22000, {"major_coverage.cjs": True}),
        _row("cheap", True, 0.10, 8000, {"major_coverage.cjs": True}),
        _row("cheap", False, 0.12, 9000, {"major_coverage.cjs": False}),
    ], baseline="base", tolerance=0)
    base, cheap = rows
    assert base["adoptable"] and base["baseline"] and base["no_worse"]
    assert not cheap["adoptable"] and not cheap["no_worse"]
    assert cheap["assertions"]["major_coverage.cjs"] == 0.5
    assert cheap["cost_vs_baseline"] == pytest.approx(0.11 / 0.42)
    text = summarise.render(rows)
    assert "WORSE THAN BASELINE: major_coverage.cjs 1/2 vs baseline 2/2" in text


def _runs(label, results):
    return [_row(label, all(r.values()), 0.2, 6000, r) for r in results]


def test_a_variant_matching_a_noisy_baseline_is_no_worse():
    """The baseline fails some checks itself; matching it is the bar, not passing everything."""
    base = _runs("base", [{"schema.cjs": True, "faithful": False},
                          {"schema.cjs": True, "faithful": True},
                          {"schema.cjs": True, "faithful": False},
                          {"schema.cjs": True, "faithful": True}])
    same = _runs("same", [{"schema.cjs": True, "faithful": True},
                          {"schema.cjs": True, "faithful": False},
                          {"schema.cjs": True, "faithful": False},
                          {"schema.cjs": True, "faithful": True}])
    rows = summarise.summarise(base + same, baseline="base")
    assert rows[1]["no_worse"] and not rows[1]["adoptable"]
    assert "NO WORSE THAN BASELINE" in summarise.render(rows)


def test_tolerance_is_in_baseline_runs():
    base = _runs("base", [{"c": True}] * 4)
    one_miss = _runs("v", [{"c": True}] * 3 + [{"c": False}])
    two_miss = _runs("w", [{"c": True}] * 2 + [{"c": False}] * 2)
    rows = {r["label"]: r for r in summarise.summarise(base + one_miss + two_miss, baseline="base")}
    assert rows["v"]["no_worse"]  # one run behind is inside the default tolerance of 1
    assert not rows["w"]["no_worse"]
    strict = {r["label"]: r for r in summarise.summarise(base + one_miss, baseline="base", tolerance=0)}
    assert not strict["v"]["no_worse"]


def test_provider_errors_count_against_a_variant():
    base = _runs("base", [{"c": True}] * 3)
    errored = _runs("v", [{"c": True}] * 3)
    errored[0]["error"] = "timeout"
    rows = summarise.summarise(base + errored, baseline="base")
    assert not rows[1]["no_worse"] and "provider error" in " ".join(rows[1]["worse_checks"])


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


LATEST_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
_MODEL_ID = re.compile(r"claude-[a-z]+-\d+(?:-\d+)?")


def test_eval_config_uses_only_latest_models():
    """Everything the promptfoo eval runs, graders included, is on the 5.5 family."""
    with open(os.path.join(EVAL_DIR, "promptfooconfig.yaml"), encoding="utf-8") as handle:
        found = set(_MODEL_ID.findall(handle.read()))
    assert found and found <= LATEST_MODELS, found - LATEST_MODELS


def test_price_table_uses_only_latest_models_apart_from_baseline():
    with open(os.path.join(EVAL_DIR, "prices.json"), encoding="utf-8") as handle:
        table = json.load(handle)
    models = {k for k in table if not k.startswith("_") and k != "baseline_prices"}
    assert models == LATEST_MODELS
    assert set(table["baseline_prices"]) - {"_comment"} == {"claude-haiku-4-5"}


def _meta():
    def article(i, source):
        return {"title": f"Story {i}", "sourceName": source}
    articles = [article(0, "A"), article(1, "B"), article(2, "C"), article(3, "A"),
                article(4, "A"), article(5, "B"), article(6, "A")]
    return {"articles": articles, "groups": [[0, 1, 2], [3], [4, 5], [6, 3]]}


def test_candidate_stories_ranks_by_distinct_feeds():
    found = make_fixture.candidate_stories(_meta(), min_feeds=2)
    assert [(c, t) for c, t, _ in found] == [(3, "Story 0"), (2, "Story 4")]
    assert found[0][2] == ["A", "B", "C"]
    # one feed repeating itself is not "many feeds ran it"
    assert make_fixture.candidate_stories(_meta(), min_feeds=3)[0][1] == "Story 0"
    assert [t for _, t, _ in make_fixture.candidate_stories(_meta(), min_feeds=3)] == ["Story 0"]


def _fixture(root, name, curated):
    folder = root / name
    folder.mkdir()
    (folder / "digest_articles.json").write_text(json.dumps(
        {"AI/ML": [{"title": "t", "url": "u", "summary": "s", "source": "x"}]}))
    if curated:
        (folder / "expected_major.json").write_text('{"must_cover": ["t"]}')


def _config(path):
    path.write_text(
        "tests:\n" + make_fixture.BEGIN_TESTS + "\n  - old\n" + make_fixture.END_TESTS + "\nafter: 1\n")


def test_sync_tests_generates_entries_writes_digest_text_and_is_idempotent(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _fixture(fixtures, "2026-11-10-haiku-4-5", curated=False)
    _fixture(fixtures, "2026-11-11-haiku-4-5", curated=True)
    (fixtures / "not-a-fixture").mkdir()
    config = tmp_path / "config.yaml"
    _config(config)

    assert make_fixture.sync_tests(str(config), str(fixtures)) is True
    text = config.read_text()
    assert "fixture: fixtures/2026-11-10-haiku-4-5/digest_articles.json" in text
    assert "expected_major: fixtures/2026-11-11-haiku-4-5/expected_major.json" in text
    assert "expected_major: fixtures/2026-11-10-haiku-4-5" not in text  # not curated yet
    assert "not-a-fixture" not in text and "- old" not in text and text.endswith("after: 1\n")
    assert (fixtures / "2026-11-10-haiku-4-5" / "digest_text.txt").read_text().startswith("## AI/ML")
    assert make_fixture.sync_tests(str(config), str(fixtures)) is False


def test_sync_tests_needs_the_markers(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("tests: []\n")
    with pytest.raises(ValueError):
        make_fixture.sync_tests(str(config), str(tmp_path))


def test_committed_config_matches_the_fixtures_directory():
    """If a fixture is added or curated without running sync-tests, this fails."""
    with open(make_fixture.CONFIG_PATH, encoding="utf-8") as handle:
        config = handle.read()
    expected = (f"{make_fixture.BEGIN_TESTS}\n"
                f"{make_fixture.render_tests(make_fixture.FIXTURES_DIR)}\n{make_fixture.END_TESTS}")
    assert expected in config


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("expected_major, passes", [
    (None, True),  # not curated: not checked
    ("fixtures/2026-06-15/expected_major.json", False),  # curated: an empty brief misses them
])
def test_major_coverage_assertion_skips_uncurated_fixtures(expected_major, passes):
    variables = {"fixture": "fixtures/2026-06-15/digest_articles.json"}
    if expected_major:
        variables["expected_major"] = expected_major
    script = ("const a=require('./assertions/major_coverage.cjs');"
              "console.log(JSON.stringify(a(JSON.stringify({categories:{}}),"
              f"{{vars:{json.dumps(variables)}}})));")
    out = subprocess.run(["node", "-e", script], cwd=EVAL_DIR, capture_output=True, text=True,
                         check=True)
    assert json.loads(out.stdout)["pass"] is passes


def test_scheduled_ci_eval_is_monthly_and_runs_only_the_production_setting():
    """The matrix is a manual cost comparison; the schedule must not run all of it."""
    workflow = os.path.join(EVAL_DIR, os.pardir, ".github", "workflows", "eval.yml")
    with open(workflow, encoding="utf-8") as handle:
        text = handle.read()
    assert "--filter-providers '^opus-5-5/high$' --repeat 1" in text
    runs = [line for line in text.splitlines() if "npm run eval" in line and "echo" not in line]
    assert len(runs) == 2 and all('"${args[@]}"' in line for line in runs)
    yaml = pytest.importorskip("yaml")
    parsed = yaml.safe_load(text)
    triggers = parsed.get("on", parsed.get(True))
    assert triggers["workflow_dispatch"]["inputs"]["matrix"]["default"] is False
    fields = triggers["schedule"][0]["cron"].split()
    assert fields[2] != "*" and fields[4] == "*"  # a fixed day of the month: at most monthly


def test_filter_to_brief_input_matches_what_production_sends():
    digest = {
        "Other": [{"title": "Not seen", "url": "https://x/1", "summary": "s", "source": "a"}],
        "Cycling": [{"title": "Ride", "url": "https://x/2", "summary": "s", "source": "a"}],
        "AI/ML": [
            {"title": "Model", "url": "https://x/3", "summary": "s", "source": "a"},
            {"title": "Ad", "url": "https://x/sponsored/4", "summary": "s", "source": "a"},
            {"title": "Sponsored: buy", "url": "https://x/5", "summary": "s", "source": "a"},
        ],
        "Science": [{"title": "Not seen either", "url": "https://x/6", "summary": "s", "source": "a"}],
    }
    out = make_fixture.filter_to_brief_input(digest, ["AI/ML", "Business"], ["Cycling"])
    assert list(out) == ["AI/ML", "Cycling"]  # themed then personal, config order, others gone
    assert [a["title"] for a in out["AI/ML"]] == ["Model"]  # sponsored dropped


def test_promptfoo_config_gives_the_faithfulness_rubric_the_reader_context():
    with open(os.path.join(EVAL_DIR, "promptfooconfig.yaml"), encoding="utf-8") as handle:
        text = handle.read()
    assert "brief_config: file://../src/rss_email/brief_config.json" in text
    assert "{{ brief_config | dump }}" in text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_major_coverage_counts_stories_listed_in_read_these():
    fixture = "fixtures/2026-06-15/digest_articles.json"
    with open(os.path.join(EVAL_DIR, fixture), encoding="utf-8") as handle:
        digest = json.load(handle)
    with open(os.path.join(EVAL_DIR, "fixtures/2026-06-15/expected_major.json"), encoding="utf-8") as handle:
        wanted = json.load(handle)["must_cover"]
    ids = {}
    n = 0
    for items in digest.values():
        for item in items:
            n += 1
            ids[item["title"]] = str(n)
    brief = {"must_read": [{"id": ids[t], "why": "x"} for t in wanted], "categories": {}}
    script = ("const a=require('./assertions/major_coverage.cjs');"
              f"console.log(JSON.stringify(a({json.dumps(json.dumps(brief))},"
              f"{{vars:{{fixture:{json.dumps(fixture)},"
              "expected_major:'fixtures/2026-06-15/expected_major.json'}})));")
    out = subprocess.run(["node", "-e", script], cwd=EVAL_DIR, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["pass"] is True


def _memory_day(date, theme="A theme", n_articles=2):
    return {
        "date": date,
        "themes": [{
            "category": "AI/ML", "theme": theme, "signal_strength": "HIGH", "tldr": "A summary.",
            "articles": [{"title": f"{theme} article {i}", "link": f"https://x/{date}/{i}"}
                         for i in range(n_articles)],
        }],
        "must_read": [],
    }


def test_memory_for_fixture_keeps_only_earlier_days_up_to_the_cap():
    memory = {"days": [_memory_day(f"2026-11-{d:02d}") for d in range(1, 21)]}
    subset = make_fixture.memory_for_fixture(memory, "2026-11-12", max_days=14)
    dates = [d["date"] for d in subset["days"]]
    assert dates[-1] == "2026-11-11" and "2026-11-12" not in dates  # strictly before
    assert len(dates) == 11 and dates[0] == "2026-11-01"  # only 11 exist before the 12th
    assert len(make_fixture.memory_for_fixture(memory, "2026-11-20", max_days=5)["days"]) == 5
    assert make_fixture.memory_for_fixture(memory, "2026-06-15")["days"] == []


def test_memory_command_writes_memory_json_and_sync_tests_points_at_it(tmp_path):
    memory_file = tmp_path / "memory.json"
    memory_file.write_text(json.dumps({"days": [_memory_day("2026-11-10"), _memory_day("2026-11-11")]}))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _fixture(fixtures, "2026-11-12-haiku-4-5", curated=False)
    _fixture(fixtures, "2026-06-15", curated=False)

    assert make_fixture.main(["make_fixture.py", "memory", str(memory_file),
                              str(fixtures / "2026-11-12-haiku-4-5"), str(fixtures / "2026-06-15")]) == 0
    written = json.loads((fixtures / "2026-11-12-haiku-4-5" / "memory.json").read_text())
    assert [d["date"] for d in written["days"]] == ["2026-11-10", "2026-11-11"]
    assert not (fixtures / "2026-06-15" / "memory.json").exists()  # nothing earlier than June 15

    config = tmp_path / "config.yaml"
    _config(config)
    make_fixture.sync_tests(str(config), str(fixtures))
    text = config.read_text()
    assert "memory_path: fixtures/2026-11-12-haiku-4-5/memory.json" in text
    assert text.count("memory_path") == 1


def _memory_file(tmp_path, dates):
    path = tmp_path / "memory.json"
    path.write_text(json.dumps({"days": [_memory_day(d, theme=f"Theme {d}") for d in dates]}))
    return str(path)


def test_provider_renders_memory_before_the_fixture_date(tmp_path):
    path = _memory_file(tmp_path, ["2026-11-08", "2026-11-09", "2026-11-10", "2026-11-12"])
    text = provider.load_previous_context(path, "2026-11-12", 14)
    assert "## 2026-11-08" in text and "## 2026-11-10" in text
    assert "2026-11-12" not in text  # the fixture's own day is never in its memory
    assert "Theme 2026-11-09: A summary." in text and "- Theme 2026-11-09 article 0" in text


def test_provider_memory_days_limits_to_the_most_recent(tmp_path):
    path = _memory_file(tmp_path, ["2026-11-08", "2026-11-09", "2026-11-10"])
    text = provider.load_previous_context(path, "2026-11-12", 2)
    assert "2026-11-08" not in text and "2026-11-09" in text and "2026-11-10" in text
    assert provider.load_previous_context(path, "2026-11-12", 0) == ""
    assert provider.load_previous_context(None, "2026-11-12", 14) == ""
    assert provider.load_previous_context(str(tmp_path / "missing.json"), "2026-11-12", 14) == ""


def test_call_api_passes_memory_to_synthesis(tmp_path):
    fixture = tmp_path / "2026-11-12" / "digest_articles.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({"AI/ML": [{"title": "t", "url": "u", "summary": "s", "source": "x"}]}))
    memory = _memory_file(tmp_path, ["2026-11-10", "2026-11-11"])
    seen = {}

    def fake_synthesize(_input, config, previous_context="", date=None):
        seen.update(previous_context=previous_context, date=date)
        return None

    with patch.object(provider, "synthesize", fake_synthesize):
        provider.call_api("", {"config": {"memory_days": 1}},
                          {"vars": {"fixture": str(fixture), "memory_path": memory}})
    assert "2026-11-11" in seen["previous_context"] and "2026-11-10" not in seen["previous_context"]
    seen.clear()
    with patch.object(provider, "synthesize", fake_synthesize):
        provider.call_api("", {}, {"vars": {"fixture": str(fixture)}})
    assert seen["previous_context"] == ""  # no memory_path: the fixture runs without memory
