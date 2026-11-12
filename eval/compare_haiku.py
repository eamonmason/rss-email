"""Assess moving grouping and categorising from Haiku 4.5 to Haiku 5.5.

    python compare_haiku.py batch-metadata/batch-20261110-073000.json [--out report.json]
        [--write-fixtures fixtures] [--raw-dir raw] [--variants claude-haiku-4-5 ...]

Input is a real ``batch-metadata/batch-*.json`` from the S3 bucket (articles plus the
groups the production run used). Fetch it with ``aws s3 cp s3://<bucket>/batch-metadata/...``.

It runs the **shipping prompt builders and parsers** (the same prompts the Lambdas send),
synchronously, for each variant. Every variant re-groups the articles and categorises the
*stored* groups (so grouping differences do not leak into the categorising comparison).

Because agreement with Haiku 4.5 is not accuracy, the report adds four checks:

* **Noise floor** - Haiku 4.5 runs twice (``claude-haiku-4-5`` and ``claude-haiku-4-5#2``).
  A candidate is judged against how much 4.5 disagrees with *itself*, not against 100%.
* **Brief-visible agreement** - only themed and personal categories reach the brief, so
  agreement is also measured on groups where either side picked one of those.
* **Blind adjudication** - ``claude-sonnet-5-5`` picks the better category for sampled
  brief-visible disagreements, with the options in random order and no model names.
  Win rates for each variant (and the 4.5-vs-4.5 calibration) are reported.
* **Grouping diagnostics** - ``parse_grouping_response`` falls back to one group per article
  when a reply is unusable. That is detected and reported as a failure, and raw replies can
  be saved with ``--raw-dir``.

``--write-fixtures`` also writes each variant's categorised digest in the brief-eval fixture
shape, so the brief eval can show whether categorisation drift changes what the brief surfaces.

Spends real API money (a few cents per day of data). Needs ``ANTHROPIC_API_KEY``.
Haiku 4.5 is the one non-5.5 model the eval tooling mentions: it is the baseline being replaced.
"""

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# pylint: disable=wrong-import-position
import anthropic  # noqa: E402

from make_fixture import digest_to_text, filter_to_brief_input  # noqa: E402
from provider import cost_usd  # noqa: E402
from rss_email.article_grouper import (  # noqa: E402
    create_grouping_prompt,
    parse_grouping_response,
)
from rss_email.article_processor import (  # noqa: E402
    PRIORITY_CATEGORIES,
    _build_group_payload,
    create_group_summary_prompt,
)
from rss_email.brief_generator import load_brief_config  # noqa: E402
from rss_email.claude_compat import response_text  # noqa: E402
from rss_email.json_utils import extract_json_from_text  # noqa: E402
from rss_email.submit_email_batch import _split_groups_into_batches  # noqa: E402

BASELINE = "claude-haiku-4-5"
DEFAULT_VARIANTS = [
    BASELINE,
    f"{BASELINE}#2",  # second baseline run: the noise floor
    "claude-haiku-5-5:low",
    "claude-haiku-5-5:medium",
]
ADJUDICATOR = "claude-sonnet-5-5"
GROUPING_MAX_TOKENS = 4096  # as in article_grouper
CATEGORISE_MAX_TOKENS = 8192  # as in submit_email_batch
ADJUDICATE_MAX_TOKENS = 4000
BATCH_SIZE = 25  # CLAUDE_BATCH_SIZE default

# Decision rule, fixed before the results were looked at (see eval/README.md).
CATEGORY_TOLERANCE = 0.05  # candidate agreement may trail 4.5's self-agreement by this much
GROUPING_TOLERANCE = 0.10  # same, for grouping pair recall
MIN_WIN_RATE = 0.45  # blind adjudication, (wins + 0.5 * ties) / n
MIN_GROUPING_COVERAGE = 0.90  # share of articles a grouping reply must actually place
# Used only when no second baseline run is available to measure the noise floor.
FALLBACK_CATEGORY_AGREEMENT = 0.90
FALLBACK_GROUPING_RECALL = 0.85


@dataclass
class Variant:
    """A model plus optional effort and tag, e.g. ``claude-haiku-5-5:low`` or ``...#2``."""

    model: str
    effort: Optional[str] = None
    tag: Optional[str] = None

    @property
    def label(self) -> str:
        """Display name."""
        base = f"{self.model}:{self.effort}" if self.effort else self.model
        return f"{base}#{self.tag}" if self.tag else base

    @classmethod
    def parse(cls, text: str) -> "Variant":
        """Parse ``model[:effort][#tag]``."""
        body, _, tag = text.partition("#")
        model, _, effort = body.partition(":")
        return cls(model, effort or None, tag or None)

    def params(self) -> Dict[str, Any]:
        """Extra request parameters for this variant."""
        return {"output_config": {"effort": self.effort}} if self.effort else {}


@dataclass
class Run:
    """What one variant produced, plus how the calls went."""

    variant: Variant
    groups: Optional[List[List[int]]] = None
    grouping_fallback: Optional[str] = None  # why the grouping reply was unusable
    nonsingleton_groups: int = 0
    categories: Dict[str, str] = field(default_factory=dict)  # group_id -> category
    summaries: Dict[str, str] = field(default_factory=dict)  # group_id -> summary
    raw: Dict[str, List[str]] = field(default_factory=dict)  # stage -> raw replies
    stage_tokens: Dict[str, List[int]] = field(default_factory=dict)  # stage -> [in, out]
    calls: int = 0
    parse_failures: int = 0
    refusals: int = 0
    max_token_stops: int = 0
    missing_groups: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ---------- pure metrics (unit-tested) ----------


def pair_set(groups: List[List[int]]) -> Set[FrozenSet[int]]:
    """All unordered article pairs that share a group."""
    return {frozenset(p) for g in groups for p in combinations(sorted(g), 2)}


def grouping_agreement(
    baseline: List[List[int]], candidate: List[List[int]]
) -> Dict[str, Optional[float]]:
    """Pair precision and recall of ``candidate`` against ``baseline``."""
    base, cand = pair_set(baseline), pair_set(candidate)
    hit = len(base & cand)
    return {
        "precision": hit / len(cand) if cand else None,
        "recall": hit / len(base) if base else None,
        "baseline_pairs": len(base),
        "candidate_pairs": len(cand),
    }


def category_agreement(
    baseline: Dict[str, str], candidate: Dict[str, str]
) -> Tuple[Optional[float], List[Tuple[str, str, str]]]:
    """Share of baseline groups the candidate put in the same category, plus the differences.

    A baseline group the candidate omitted counts as a disagreement (candidate ``<missing>``).
    """
    if not baseline:
        return None, []
    diffs = [
        (gid, cat, candidate.get(gid, "<missing>"))
        for gid, cat in baseline.items()
        if candidate.get(gid) != cat
    ]
    return 1 - len(diffs) / len(baseline), diffs


def visible_agreement(
    baseline: Dict[str, str], candidate: Dict[str, str], visible: Set[str]
) -> Tuple[Optional[float], int]:
    """Agreement over groups either side placed in a brief-visible category.

    Returns ``(agreement, flips)`` where ``flips`` counts groups that moved into or out of
    the brief's categories. Flips among invisible categories (Other, Science, ...) are ignored.
    """
    keys = [g for g, c in baseline.items() if c in visible or candidate.get(g) in visible]
    if not keys:
        return None, 0
    same = sum(1 for g in keys if candidate.get(g) == baseline[g])
    flips = sum(1 for g in keys if (baseline[g] in visible) != (candidate.get(g) in visible))
    return same / len(keys), flips


def mean_words(summaries: Dict[str, str]) -> Optional[float]:
    """Mean summary length in words."""
    if not summaries:
        return None
    return sum(len(s.split()) for s in summaries.values()) / len(summaries)


def grouping_fallback_reason(text: str, article_count: int) -> Optional[str]:
    """Why a grouping reply would make ``parse_grouping_response`` fall back to singletons.

    Returns None when the reply is usable. A reply that parses but places fewer than
    ``MIN_GROUPING_COVERAGE`` of the articles also counts: the rest become singletons silently.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = extract_json_from_text(text, required_fields=["groups"])
    if not isinstance(data, dict):
        return "unparseable reply"
    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list):
        return "no 'groups' list in reply"
    placed: Set[int] = set()
    for group in raw_groups:
        for ref in group if isinstance(group, list) else []:
            if isinstance(ref, str) and ref.startswith("article_"):
                try:
                    idx = int(ref.split("_", 1)[1])
                except ValueError:
                    continue
                if 0 <= idx < article_count:
                    placed.add(idx)
    coverage = len(placed) / article_count if article_count else 1.0
    if coverage < MIN_GROUPING_COVERAGE:
        return f"reply places only {coverage:.0%} of articles"
    return None


def win_rate(counts: Dict[str, int]) -> Optional[float]:
    """Candidate win rate from adjudication counts ``{"x": wins, "y": wins, "tie": ties}``."""
    decided = counts.get("x", 0) + counts.get("y", 0) + counts.get("tie", 0)
    if not decided:
        return None
    return (counts.get("x", 0) + 0.5 * counts.get("tie", 0)) / decided


def verdict(run: Run, metrics: Dict[str, Optional[float]]) -> List[str]:
    """Reasons a variant misses the decision rule (empty list means it qualifies).

    ``metrics`` keys: cat_all, noise_all, cat_vis, noise_vis, recall, noise_recall, win_rate.
    A ``noise_*`` of None means no second baseline run: the fixed fallback bars apply.
    """
    reasons = []
    if run.parse_failures:
        reasons.append(f"{run.parse_failures} unparseable response(s)")
    if run.refusals:
        reasons.append(f"{run.refusals} refusal(s)")
    if run.max_token_stops:
        reasons.append(f"{run.max_token_stops} max_tokens stop(s)")
    if run.missing_groups:
        reasons.append(f"{run.missing_groups} group(s) missing from the response")
    if run.groups is None:
        reasons.append("grouping failed")
    if run.grouping_fallback:
        reasons.append(f"grouping fell back to singletons ({run.grouping_fallback})")

    def floor(noise: Optional[float], fixed: float, tolerance: float) -> float:
        return fixed if noise is None else noise - tolerance

    for key, noise_key, label in (("cat_all", "noise_all", "category agreement"),
                                  ("cat_vis", "noise_vis", "brief-visible agreement")):
        value = metrics.get(key)
        bar = floor(metrics.get(noise_key), FALLBACK_CATEGORY_AGREEMENT, CATEGORY_TOLERANCE)
        if value is not None and value < bar:
            reasons.append(f"{label} {value:.0%} < {bar:.0%}")
    recall = metrics.get("recall")
    recall_bar = floor(metrics.get("noise_recall"), FALLBACK_GROUPING_RECALL, GROUPING_TOLERANCE)
    if recall is not None and recall < recall_bar:
        reasons.append(f"grouping recall {recall:.0%} < {recall_bar:.0%}")
    wins = metrics.get("win_rate")
    if wins is not None and wins < MIN_WIN_RATE:
        reasons.append(f"adjudication win rate {wins:.0%} < {MIN_WIN_RATE:.0%}")
    return reasons


# ---------- live calls ----------


def _call(client: Any, run: Run, stage: str, prompt: str, max_tokens: int) -> Optional[str]:
    """One synchronous call; records usage and failure modes. Returns text or None."""
    run.calls += 1
    message = client.messages.create(
        model=run.variant.model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **run.variant.params(),
    )
    used = (message.usage.input_tokens, message.usage.output_tokens)
    run.input_tokens += used[0]
    run.output_tokens += used[1]
    tokens = run.stage_tokens.setdefault(stage, [0, 0])
    tokens[0] += used[0]
    tokens[1] += used[1]
    if message.stop_reason == "refusal":
        run.refusals += 1
        return None
    if message.stop_reason == "max_tokens":
        run.max_token_stops += 1
    text = response_text(message)
    run.raw.setdefault(stage, []).append(text)
    if not text:
        run.parse_failures += 1
        return None
    return text


def run_grouping(client: Any, run: Run, articles: List[Dict[str, Any]]) -> None:
    """Group all articles in one call, as the production grouping step does."""
    text = _call(client, run, "grouping", create_grouping_prompt(articles), GROUPING_MAX_TOKENS)
    if not text:
        run.groups, run.grouping_fallback = None, "no usable reply"
        return
    run.grouping_fallback = grouping_fallback_reason(text, len(articles))
    run.groups = parse_grouping_response(text, len(articles))
    run.nonsingleton_groups = sum(1 for g in run.groups if len(g) > 1)


def run_categorise(
    client: Any, run: Run, articles: List[Dict[str, Any]], groups: List[List[int]]
) -> None:
    """Categorise and summarise the given groups in batches of 25, as production does."""
    for batch in _split_groups_into_batches(groups, BATCH_SIZE):
        payloads = [_build_group_payload(gid, idx, articles) for gid, idx in batch]
        text = _call(client, run, "categorise", create_group_summary_prompt(payloads),
                     CATEGORISE_MAX_TOKENS)
        data = extract_json_from_text(text, required_fields=["categories"]) if text else None
        if data is None:
            run.parse_failures += 1
            run.missing_groups += len(batch)
            continue
        seen = set()
        for category, entries in data["categories"].items():  # pylint: disable=unsubscriptable-object
            for entry in entries or []:
                gid = entry.get("group_id")
                if gid:
                    seen.add(gid)
                    run.categories[gid] = entry.get("category") or category
                    run.summaries[gid] = entry.get("summary", "")
        run.missing_groups += len({gid for gid, _ in batch} - seen)


# ---------- blind adjudication ----------


def build_adjudication_prompt(items: List[Dict[str, Any]]) -> str:
    """Prompt asking which of two categories fits each article better. Names no model."""
    return (
        "You are checking how RSS articles were categorised. For each article below, two "
        "categories were proposed (option_a and option_b). Decide which one fits the article "
        "better, judged only on the article itself. Answer TIE if both are equally reasonable.\n\n"
        f"Available categories: {', '.join(PRIORITY_CATEGORIES)}\n\n"
        "Return ONLY valid JSON, no markdown: "
        '{"choices": [{"id": "<id>", "choice": "A" | "B" | "TIE"}]} with one entry per article.\n\n'
        f"Articles:\n{json.dumps(items, indent=2)}\n"
    )


def adjudicate(
    client: Any,
    pairs: List[Tuple[str, str, str]],
    articles: List[Dict[str, Any]],
    groups: List[List[int]],
    rng: random.Random,
    usage: Optional[List[int]] = None,
) -> Dict[str, int]:
    """Blind-judge ``(group_id, category_x, category_y)`` pairs.

    The two categories are shown in random order. Returns counts ``{"x", "y", "tie",
    "invalid"}`` keyed by which side won, regardless of display order. ``usage`` (a two-item
    list) accumulates input/output tokens.
    """
    counts = {"x": 0, "y": 0, "tie": 0, "invalid": 0}
    for start in range(0, len(pairs), BATCH_SIZE):
        chunk = pairs[start:start + BATCH_SIZE]
        items, x_is_a = [], {}
        for gid, cat_x, cat_y in chunk:
            swap = rng.random() < 0.5
            x_is_a[gid] = not swap
            first, second = (cat_y, cat_x) if swap else (cat_x, cat_y)
            members = _build_group_payload(gid, groups[int(gid.split("_", 1)[1])], articles)["members"]
            items.append({"id": gid, "article": members, "option_a": first, "option_b": second})
        message = client.messages.create(
            model=ADJUDICATOR,
            max_tokens=ADJUDICATE_MAX_TOKENS,
            messages=[{"role": "user", "content": build_adjudication_prompt(items)}],
            output_config={"effort": "low"},
        )
        if usage is not None:
            usage[0] += message.usage.input_tokens
            usage[1] += message.usage.output_tokens
        data = extract_json_from_text(response_text(message), required_fields=["choices"])
        answers = {}
        for entry in (data or {}).get("choices", []):  # pylint: disable=no-member
            if isinstance(entry, dict):
                answers[entry.get("id")] = str(entry.get("choice", "")).upper()
        for gid, _, _ in chunk:
            choice = answers.get(gid)
            if choice == "TIE":
                counts["tie"] += 1
            elif choice in ("A", "B"):
                counts["x" if (choice == "A") == x_is_a[gid] else "y"] += 1
            else:
                counts["invalid"] += 1
    return counts


def visible_disagreements(
    diffs: List[Tuple[str, str, str]], visible: Set[str]
) -> List[Tuple[str, str, str]]:
    """Disagreements where either category is brief-visible and the candidate answered."""
    return [d for d in diffs if (d[1] in visible or d[2] in visible) and d[2] != "<missing>"]


# ---------- output ----------


def to_fixture(
    articles: List[Dict[str, Any]], groups: List[List[int]], run: Run,
    brief_categories: Optional[Tuple[List[str], List[str]]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Render a variant's categorised groups in the brief-eval fixture shape.

    With ``brief_categories`` (themed, personal) the result is reduced to what the brief
    receives in production (see ``make_fixture.filter_to_brief_input``).
    """
    digest: Dict[str, List[Dict[str, str]]] = {}
    for n, members in enumerate(groups):
        gid = f"group_{n}"
        category = run.categories.get(gid)
        if not category:
            continue
        primary = articles[members[0]]
        digest.setdefault(category, []).append({
            "title": primary.get("title", ""),
            "url": primary.get("link", ""),
            "summary": run.summaries.get(gid, ""),
            "source": primary.get("sourceName") or "",
        })
    if brief_categories is not None:
        return filter_to_brief_input(digest, *brief_categories)
    return digest


def run_cost(run: Run, stage: Optional[str] = None) -> Optional[float]:
    """Synchronous-price cost of a run, or of one stage (the Batch API halves categorising)."""
    if stage:
        tokens = run.stage_tokens.get(stage, [0, 0])
        return cost_usd(run.variant.model, tokens[0], tokens[1])
    return cost_usd(run.variant.model, run.input_tokens, run.output_tokens)


def evaluate(
    runs: List[Run],
    stored_groups: List[List[int]],
    visible: Set[str],
    adjudications: Dict[str, Dict[str, int]],
) -> Dict[str, Dict[str, Any]]:
    """Compute metrics and the decision-rule verdict for every run against ``runs[0]``."""
    base = runs[0]
    noise_run = next((r for r in runs[1:] if r.variant.model == base.variant.model), None)
    noise_all = noise_vis = None
    if noise_run:
        noise_all = category_agreement(base.categories, noise_run.categories)[0]
        noise_vis = visible_agreement(base.categories, noise_run.categories, visible)[0]
    noise_recall = None
    if base.groups is not None:
        noise_recall = grouping_agreement(stored_groups, base.groups)["recall"]

    results: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        label = run.variant.label
        agree, diffs = category_agreement(base.categories, run.categories)
        vis, flips = visible_agreement(base.categories, run.categories, visible)
        grouping = grouping_agreement(stored_groups, run.groups) if run.groups is not None else {}
        counts = adjudications.get(label)
        metrics = {
            "cat_all": agree, "noise_all": noise_all, "cat_vis": vis, "noise_vis": noise_vis,
            "recall": grouping.get("recall"), "noise_recall": noise_recall,
            "win_rate": win_rate(counts) if counts else None,
        }
        is_reference = run is base or run is noise_run
        results[label] = {
            "metrics": metrics, "visible_flips": flips, "diffs": diffs,
            "grouping": grouping, "adjudication": counts,
            "failures": [] if is_reference else verdict(run, metrics),
            "reference": is_reference,
        }
    return results


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def report(
    runs: List[Run], results: Dict[str, Dict[str, Any]], visible: Set[str], names: Dict[str, str]
) -> str:
    """Build the text report."""
    lines = []
    for run in runs:
        res = results[run.variant.label]
        cost, group_cost = run_cost(run), run_cost(run, "grouping")
        metrics = res["metrics"]
        lines.append(f"== {run.variant.label}")
        lines.append(
            f"   calls {run.calls}, tokens in/out {run.input_tokens}/{run.output_tokens}, "
            f"sync cost {'n/a' if cost is None else f'${cost:.4f}'} "
            f"(grouping {'n/a' if group_cost is None else f'${group_cost:.4f}'}), "
            f"mean summary {mean_words(run.summaries) or 0:.0f} words, "
            f"multi-article groups {run.nonsingleton_groups}"
        )
        lines.append(
            f"   grouping recall vs stored {_pct(metrics['recall'])}"
            + (f", FALLBACK: {run.grouping_fallback}" if run.grouping_fallback else "")
        )
        if run.variant.label != runs[0].variant.label:
            lines.append(
                f"   category agreement {_pct(metrics['cat_all'])} (brief-visible "
                f"{_pct(metrics['cat_vis'])}, {res['visible_flips']} in/out of the brief)"
            )
        counts = res["adjudication"]
        if counts:
            lines.append(
                f"   blind adjudication vs {runs[0].variant.label}: wins {counts['x']}, "
                f"losses {counts['y']}, ties {counts['tie']}, invalid {counts['invalid']} "
                f"-> win rate {_pct(metrics['win_rate'])}"
            )
        if res["reference"]:
            lines.append("   (reference run: baseline or noise floor)")
        else:
            lines.append("   QUALIFIES" if not res["failures"]
                         else "   DOES NOT QUALIFY: " + "; ".join(res["failures"]))
            shown = visible_disagreements(res["diffs"], visible) or res["diffs"]
            for gid, was, now in shown[:25]:
                lines.append(f"     {gid}: {was} -> {now}  | {names.get(gid, '')[:70]}")
    base_metrics = next((r for r in results.values() if r["metrics"]["noise_all"] is not None), None)
    if base_metrics:
        m = base_metrics["metrics"]
        lines.append(
            f"\nNoise floor (Haiku 4.5 vs itself): category agreement {_pct(m['noise_all'])}, "
            f"brief-visible {_pct(m['noise_vis'])}; grouping recall vs stored {_pct(m['noise_recall'])}"
        )
    return "\n".join(lines)


def write_raw(runs: List[Run], directory: str) -> None:
    """Save every raw model reply so a bad one can be read."""
    os.makedirs(directory, exist_ok=True)
    for run in runs:
        tag = run.variant.label.replace("claude-", "").replace(":", "-").replace("#", "-")
        for stage, replies in run.raw.items():
            for n, text in enumerate(replies):
                path = os.path.join(directory, f"{tag}-{stage}-{n}.txt")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text)


def main(argv: List[str]) -> int:  # pylint: disable=too-many-locals
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("metadata", help="batch-metadata/batch-*.json downloaded from S3")
    parser.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS)
    parser.add_argument("--out", help="write the JSON summary here")
    parser.add_argument("--raw-dir", help="save every raw model reply here")
    parser.add_argument("--write-fixtures", metavar="DIR",
                        help="write each variant's categorised digest as a brief-eval fixture")
    parser.add_argument("--no-adjudicate", action="store_true", help="skip the blind adjudication")
    parser.add_argument("--adjudication-sample", type=int, default=100,
                        help="max disagreements judged per candidate (default 100)")
    args = parser.parse_args(argv)

    with open(args.metadata, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    if not isinstance(meta, dict) or "articles" not in meta or "groups" not in meta:
        parser.error(
            f"{args.metadata} is not a batch-metadata file (expected 'articles' and 'groups'); "
            "use the files from s3://<bucket>/batch-metadata/ and keep reports elsewhere"
        )
    articles, stored_groups = meta["articles"], meta["groups"]
    variants = [Variant.parse(v) for v in args.variants]
    if variants[0].model != BASELINE:
        parser.error(f"the first variant must be the baseline, {BASELINE}")
    config = load_brief_config()
    visible = set(config.get("themed_categories", [])) | set(config.get("personal_categories", []))

    client = anthropic.Anthropic()
    runs = []
    for variant in variants:
        run = Run(variant)
        print(f"running {variant.label} ...", file=sys.stderr)
        run_grouping(client, run, articles)
        run_categorise(client, run, articles, stored_groups)
        runs.append(run)

    base = runs[0]
    rng = random.Random(0)
    adjudications: Dict[str, Dict[str, int]] = {}
    adjudicator_usage = [0, 0]
    if not args.no_adjudicate:
        for run in runs[1:]:
            _, diffs = category_agreement(base.categories, run.categories)
            pairs = visible_disagreements(diffs, visible)
            if len(pairs) > args.adjudication_sample:
                pairs = rng.sample(pairs, args.adjudication_sample)
            if pairs:
                print(f"adjudicating {len(pairs)} disagreements for {run.variant.label} ...",
                      file=sys.stderr)
                adjudications[run.variant.label] = adjudicate(
                    client, pairs, articles, stored_groups, rng, adjudicator_usage
                )

    names = {f"group_{n}": articles[g[0]].get("title", "") for n, g in enumerate(stored_groups)}
    results = evaluate(runs, stored_groups, visible, adjudications)
    print(report(runs, results, visible, names))
    adjudicator_cost = cost_usd(ADJUDICATOR, adjudicator_usage[0], adjudicator_usage[1])
    if adjudicator_cost:
        print(f"adjudicator cost ${adjudicator_cost:.4f}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2, default=str)
    if args.raw_dir:
        write_raw(runs, args.raw_dir)
    if args.write_fixtures:
        date = (meta.get("submitted_at") or "unknown")[:10]
        for run in runs:
            tag = run.variant.label.replace("claude-", "").replace(":", "-").replace("#", "-")
            folder = os.path.join(args.write_fixtures, f"{date}-{tag}")
            os.makedirs(folder, exist_ok=True)
            digest = to_fixture(articles, stored_groups, run, (
                config.get("themed_categories", []), config.get("personal_categories", [])))
            with open(os.path.join(folder, "digest_articles.json"), "w", encoding="utf-8") as fh:
                json.dump(digest, fh, indent=2)
            with open(os.path.join(folder, "digest_text.txt"), "w", encoding="utf-8") as fh:
                fh.write(digest_to_text(digest))
            print(f"wrote {folder} (add expected_major.json by hand before running the brief eval)",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
