"""Summarise a promptfoo results file by provider (model/effort variant).

    npm run eval -- --output results.json
    python summarise.py results.json [--baseline opus-5-5/high] [--tolerance 1]

Per variant: runs passed, passes for each check next to the baseline's, mean output
tokens and mean cost per run, and the cost relative to the baseline.

The baseline itself fails some checks on some runs (a small fidelity slip, a rough
source-ranking heuristic, a story dropped now and then), so "passed every run" cannot be
the bar. A variant is "no worse than baseline" when, for every check, its pass rate trails
the baseline's by no more than ``--tolerance`` runs' worth (default 1, in baseline runs)
and it has no more provider errors than the baseline. With few runs that is a screen, not
proof: read the counts.
"""

import argparse
import json
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional


def _label(result: Dict[str, Any]) -> str:
    provider = result.get("provider") or {}
    return provider.get("label") or provider.get("id") or "unknown"


def _assertion_name(component: Dict[str, Any]) -> str:
    assertion = component.get("assertion") or {}
    value = str(assertion.get("value", ""))
    if assertion.get("type") == "javascript":
        return value.rsplit("/", 1)[-1]
    first_line = next((ln.strip() for ln in value.splitlines() if ln.strip()), "")
    return f"{assertion.get('type', '?')}: {first_line[:40]}".strip()


def worse_checks(
    row: Dict[str, Any], base: Dict[str, Any], tolerance: float
) -> List[str]:
    """Checks where ``row`` trails ``base`` by more than ``tolerance`` baseline runs."""
    reasons = []
    for name, (base_pass, base_total) in base["counts"].items():
        if not base_total:
            continue
        passes, total = row["counts"].get(name, (0, 0))
        base_rate = base_pass / base_total
        rate = passes / total if total else 0.0
        if rate < base_rate - tolerance / base_total:
            reasons.append(f"{name} {passes}/{total} vs baseline {base_pass}/{base_total}")
    if row["errors"] > base["errors"]:
        reasons.append(f"{row['errors']} provider error(s) vs baseline {base['errors']}")
    return reasons


def summarise(
    results: List[Dict[str, Any]], baseline: Optional[str] = None, tolerance: float = 1.0
) -> List[Dict[str, Any]]:
    """Group promptfoo result rows by provider label and compute the summary rows."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[_label(row)].append(row)
    if not groups:
        return []
    baseline = baseline if baseline in groups else next(iter(groups))

    rows = []
    for label, runs in groups.items():
        counts: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
        for run in runs:
            for comp in (run.get("gradingResult") or {}).get("componentResults") or []:
                tally = counts[_assertion_name(comp)]
                tally[0] += 1 if comp.get("pass") else 0
                tally[1] += 1
        costs = [run.get("cost") for run in runs if isinstance(run.get("cost"), (int, float))]
        outputs = [
            ((run.get("response") or {}).get("tokenUsage") or {}).get("completion")
            for run in runs
        ]
        outputs = [o for o in outputs if isinstance(o, (int, float))]
        rows.append({
            "label": label,
            "runs": len(runs),
            "passed": sum(1 for run in runs if run.get("success")),
            "errors": sum(1 for run in runs if run.get("error")),
            "counts": {k: (v[0], v[1]) for k, v in counts.items()},
            "assertions": {k: v[0] / v[1] for k, v in counts.items()},
            "mean_cost": sum(costs) / len(costs) if costs else None,
            "mean_output_tokens": sum(outputs) / len(outputs) if outputs else None,
        })
    by_label = {row["label"]: row for row in rows}
    base = by_label[baseline]
    for row in rows:
        mean = row["mean_cost"]
        row["cost_vs_baseline"] = mean / base["mean_cost"] if mean is not None and base["mean_cost"] else None
        row["adoptable"] = row["runs"] > 0 and row["passed"] == row["runs"] and row["errors"] == 0
        row["baseline"] = row["label"] == baseline
        row["worse_checks"] = [] if row["baseline"] else worse_checks(row, base, tolerance)
        row["no_worse"] = row["baseline"] or not row["worse_checks"]
    return rows


def _render_counts(row: Dict[str, Any], base: Dict[str, Any]) -> List[str]:
    lines = []
    for name, (passes, total) in sorted(row["counts"].items()):
        base_pass, base_total = base["counts"].get(name, (0, 0))
        extra = "" if row["baseline"] else f"   (baseline {base_pass}/{base_total})"
        lines.append(f"    {passes:>3}/{total:<3} {name}{extra}")
    return lines


def render(rows: List[Dict[str, Any]]) -> str:
    """Format summary rows as a plain-text report."""
    base = next((r for r in rows if r["baseline"]), rows[0] if rows else None)
    out = []
    for row in rows:
        cost = f"${row['mean_cost']:.3f}" if row["mean_cost"] is not None else "n/a"
        rel = f"{row['cost_vs_baseline']:.0%} of baseline" if row["cost_vs_baseline"] is not None else ""
        if row["baseline"]:
            verdict = "BASELINE"
        elif row["no_worse"]:
            verdict = "NO WORSE THAN BASELINE (within tolerance)"
        else:
            verdict = "WORSE THAN BASELINE: " + "; ".join(row["worse_checks"])
        tokens = row["mean_output_tokens"]
        out.append(f"{row['label']}: {row['passed']}/{row['runs']} runs passed all checks, "
                   f"{row['errors']} error(s), mean cost {cost} {rel}, "
                   f"mean output tokens {'n/a' if tokens is None else f'{tokens:.0f}'}")
        out.append(f"  -> {verdict}")
        out.extend(_render_counts(row, base))
    return "\n".join(out)


def main(argv: List[str]) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("results")
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--tolerance", type=float, default=1.0,
                        help="runs' worth (of baseline runs) a check may trail by (default 1)")
    args = parser.parse_args(argv)
    with open(args.results, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = summarise((data.get("results") or {}).get("results") or [], args.baseline, args.tolerance)
    print(render(rows) if rows else "No results found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
