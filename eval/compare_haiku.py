"""Assess moving grouping and categorising from Haiku 4.5 to Haiku 5.5.

    python compare_haiku.py batch-metadata/batch-20261110-073000.json [--out report.json]
        [--write-fixtures fixtures] [--variants claude-haiku-4-5 claude-haiku-5-5:low ...]

Input is a real ``batch-metadata/batch-*.json`` from the S3 bucket (articles plus the
groups the production run used). Run it with ``aws s3 cp s3://<bucket>/batch-metadata/...``.

It runs the **shipping prompt builders and parsers** (the same prompts the Lambdas send),
synchronously, for each variant, and compares against the production baseline:

* **Grouping**: the baseline is the ``groups`` stored in the metadata (what Haiku 4.5
  produced that day). Candidates re-group the same articles; agreement is measured on
  article pairs (precision / recall of "these two are the same story").
* **Categorising**: every variant categorises the *same* groups (the stored ones), so
  grouping differences do not leak in. Agreement is per group category against the baseline
  variant's categories.

Agreement with Haiku 4.5 is not ground truth - read the disagreement list. ``--write-fixtures``
also writes each variant's categorised digest in the brief-eval fixture shape, so the existing
brief eval can show whether categorisation drift changes what the brief surfaces.

Spends real API money (a few cents per day of data). Needs ``ANTHROPIC_API_KEY``.
Haiku 4.5 is the one non-5.5 model the eval tooling mentions: it is the baseline being replaced.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# pylint: disable=wrong-import-position
import anthropic  # noqa: E402

from make_fixture import digest_to_text  # noqa: E402
from provider import cost_usd  # noqa: E402
from rss_email.article_grouper import (  # noqa: E402
    create_grouping_prompt,
    parse_grouping_response,
)
from rss_email.article_processor import (  # noqa: E402
    _build_group_payload,
    create_group_summary_prompt,
)
from rss_email.claude_compat import response_text  # noqa: E402
from rss_email.json_utils import extract_json_from_text  # noqa: E402
from rss_email.submit_email_batch import _split_groups_into_batches  # noqa: E402

BASELINE = "claude-haiku-4-5"
DEFAULT_VARIANTS = [BASELINE, "claude-haiku-5-5:low", "claude-haiku-5-5:medium"]
GROUPING_MAX_TOKENS = 4096  # as in article_grouper
CATEGORISE_MAX_TOKENS = 8192  # as in submit_email_batch
BATCH_SIZE = 25  # CLAUDE_BATCH_SIZE default

# Pass bar, agreed before looking at results.
MIN_CATEGORY_AGREEMENT = 0.90
MIN_GROUPING_RECALL = 0.85


@dataclass
class Variant:
    """A model plus optional effort, e.g. ``claude-haiku-5-5:low``."""

    model: str
    effort: Optional[str] = None

    @property
    def label(self) -> str:
        """Display name."""
        return f"{self.model}:{self.effort}" if self.effort else self.model

    @classmethod
    def parse(cls, text: str) -> "Variant":
        """Parse ``model`` or ``model:effort``."""
        model, _, effort = text.partition(":")
        return cls(model, effort or None)

    def params(self) -> Dict[str, Any]:
        """Extra request parameters for this variant."""
        return {"output_config": {"effort": self.effort}} if self.effort else {}


@dataclass
class Run:
    """What one variant produced, plus how the calls went."""

    variant: Variant
    groups: Optional[List[List[int]]] = None
    categories: Dict[str, str] = field(default_factory=dict)  # group_id -> category
    summaries: Dict[str, str] = field(default_factory=dict)  # group_id -> summary
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


def mean_words(summaries: Dict[str, str]) -> Optional[float]:
    """Mean summary length in words."""
    if not summaries:
        return None
    return sum(len(s.split()) for s in summaries.values()) / len(summaries)


def verdict(run: Run, cat_agree: Optional[float], group_recall: Optional[float]) -> List[str]:
    """Reasons a variant misses the pass bar (empty list means it passes)."""
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
    if cat_agree is not None and cat_agree < MIN_CATEGORY_AGREEMENT:
        reasons.append(f"category agreement {cat_agree:.0%} < {MIN_CATEGORY_AGREEMENT:.0%}")
    if group_recall is not None and group_recall < MIN_GROUPING_RECALL:
        reasons.append(f"grouping recall {group_recall:.0%} < {MIN_GROUPING_RECALL:.0%}")
    return reasons


# ---------- live calls ----------


def _call(client: Any, run: Run, prompt: str, max_tokens: int) -> Optional[str]:
    """One synchronous call; records usage and failure modes. Returns text or None."""
    run.calls += 1
    message = client.messages.create(
        model=run.variant.model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        **run.variant.params(),
    )
    run.input_tokens += message.usage.input_tokens
    run.output_tokens += message.usage.output_tokens
    if message.stop_reason == "refusal":
        run.refusals += 1
        return None
    if message.stop_reason == "max_tokens":
        run.max_token_stops += 1
    text = response_text(message)
    if not text:
        run.parse_failures += 1
        return None
    return text


def run_grouping(client: Any, run: Run, articles: List[Dict[str, Any]]) -> None:
    """Group all articles in one call, as the production grouping step does."""
    text = _call(client, run, create_grouping_prompt(articles), GROUPING_MAX_TOKENS)
    run.groups = parse_grouping_response(text, len(articles)) if text else None


def run_categorise(
    client: Any, run: Run, articles: List[Dict[str, Any]], groups: List[List[int]]
) -> None:
    """Categorise and summarise the given groups in batches of 25, as production does."""
    for batch in _split_groups_into_batches(groups, BATCH_SIZE):
        payloads = [_build_group_payload(gid, idx, articles) for gid, idx in batch]
        text = _call(client, run, create_group_summary_prompt(payloads), CATEGORISE_MAX_TOKENS)
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


# ---------- output ----------


def to_fixture(
    articles: List[Dict[str, Any]], groups: List[List[int]], run: Run
) -> Dict[str, List[Dict[str, str]]]:
    """Render a variant's categorised groups in the brief-eval fixture shape."""
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
    return digest


def run_cost(run: Run) -> Optional[float]:
    """Synchronous-price cost of a run (the Batch API halves the categorise part)."""
    return cost_usd(run.variant.model, run.input_tokens, run.output_tokens)


def report(
    runs: List[Run], baseline_run: Run, base_groups: List[List[int]], names: Dict[str, str]
) -> Tuple[str, Dict[str, Any]]:
    """Build the text report and a JSON-able summary."""
    lines, summary = [], {}
    for run in runs:
        agree, diffs = category_agreement(baseline_run.categories, run.categories)
        grouping = (
            grouping_agreement(base_groups, run.groups) if run.groups is not None else {}
        )
        reasons = [] if run.variant.label == baseline_run.variant.label else verdict(
            run, agree, grouping.get("recall")
        )
        cost = run_cost(run)
        lines.append(f"== {run.variant.label}")
        lines.append(
            f"   calls {run.calls}, tokens in/out {run.input_tokens}/{run.output_tokens}, "
            f"sync cost {'n/a' if cost is None else f'${cost:.4f}'}, "
            f"mean summary {mean_words(run.summaries) or 0:.0f} words"
        )
        if run.variant.label != baseline_run.variant.label:
            lines.append(
                f"   category agreement {agree if agree is None else f'{agree:.1%}'}; grouping "
                f"precision {grouping.get('precision')} recall {grouping.get('recall')}"
            )
            lines.append("   PASS" if not reasons else "   FAIL: " + "; ".join(reasons))
            for gid, was, now in diffs[:25]:
                lines.append(f"     {gid}: {was} -> {now}  | {names.get(gid, '')[:70]}")
        summary[run.variant.label] = {
            "calls": run.calls, "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens, "cost": cost,
            "category_agreement": agree, "grouping": grouping,
            "failures": reasons, "disagreements": diffs,
        }
    return "\n".join(lines), summary


def main(argv: List[str]) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("metadata", help="batch-metadata/batch-*.json downloaded from S3")
    parser.add_argument("--variants", nargs="+", default=DEFAULT_VARIANTS)
    parser.add_argument("--out", help="write the JSON summary here")
    parser.add_argument("--write-fixtures", metavar="DIR",
                        help="write each variant's categorised digest as a brief-eval fixture")
    args = parser.parse_args(argv)

    with open(args.metadata, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    articles, base_groups = meta["articles"], meta["groups"]
    variants = [Variant.parse(v) for v in args.variants]
    if variants[0].model != BASELINE:
        parser.error(f"the first variant must be the baseline, {BASELINE}")

    client = anthropic.Anthropic()
    runs = []
    for variant in variants:
        run = Run(variant)
        print(f"running {variant.label} ...", file=sys.stderr)
        if variant is not variants[0]:
            run_grouping(client, run, articles)
        else:
            run.groups = base_groups  # what production produced that day
        run_categorise(client, run, articles, base_groups)
        runs.append(run)

    names = {f"group_{n}": articles[g[0]].get("title", "") for n, g in enumerate(base_groups)}
    text, summary = report(runs, runs[0], base_groups, names)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
    if args.write_fixtures:
        date = (meta.get("submitted_at") or "unknown")[:10]
        for run in runs:
            tag = run.variant.label.replace("claude-", "").replace(":", "-")
            folder = os.path.join(args.write_fixtures, f"{date}-{tag}")
            os.makedirs(folder, exist_ok=True)
            digest = to_fixture(articles, base_groups, run)
            with open(os.path.join(folder, "digest_articles.json"), "w", encoding="utf-8") as fh:
                json.dump(digest, fh, indent=2)
            with open(os.path.join(folder, "digest_text.txt"), "w", encoding="utf-8") as fh:
                fh.write(digest_to_text(digest))
            print(f"wrote {folder} (add expected_major.json by hand before running the brief eval)",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
