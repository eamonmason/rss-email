"""Summarise a promptfoo results file by provider (model/effort variant).

    npm run eval -- --output results.json
    python summarise.py results.json [--baseline opus-5-5/high]

Per variant: runs passed, pass rate for each assertion, mean output tokens and mean
cost per run, and the cost relative to the baseline. A variant is only worth
adopting when it passed **every** run (all fixtures, all repeats) - one miss in
three is the failure this eval exists to catch.
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


def summarise(results: List[Dict[str, Any]], baseline: Optional[str] = None) -> List[Dict[str, Any]]:
    """Group promptfoo result rows by provider label and compute the summary rows."""
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in results:
        groups[_label(row)].append(row)
    if not groups:
        return []
    baseline = baseline if baseline in groups else next(iter(groups))

    rows = []
    for label, runs in groups.items():
        assertions: Dict[str, List[bool]] = defaultdict(list)
        for run in runs:
            for comp in (run.get("gradingResult") or {}).get("componentResults") or []:
                assertions[_assertion_name(comp)].append(bool(comp.get("pass")))
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
            "assertions": {k: sum(v) / len(v) for k, v in assertions.items()},
            "mean_cost": sum(costs) / len(costs) if costs else None,
            "mean_output_tokens": sum(outputs) / len(outputs) if outputs else None,
        })
    base_cost = next((r["mean_cost"] for r in rows if r["label"] == baseline), None)
    for row in rows:
        mean = row["mean_cost"]
        row["cost_vs_baseline"] = mean / base_cost if mean is not None and base_cost else None
        row["adoptable"] = row["runs"] > 0 and row["passed"] == row["runs"] and row["errors"] == 0
        row["baseline"] = row["label"] == baseline
    return rows


def render(rows: List[Dict[str, Any]]) -> str:
    """Format summary rows as a plain-text report."""
    out = []
    for row in rows:
        cost = f"${row['mean_cost']:.3f}" if row["mean_cost"] is not None else "n/a"
        rel = f"{row['cost_vs_baseline']:.0%} of baseline" if row["cost_vs_baseline"] is not None else ""
        verdict = "ALL RUNS PASS" if row["adoptable"] else "NOT SAFE: failed or errored runs"
        tag = " (baseline)" if row["baseline"] else ""
        out.append(f"{row['label']}{tag}: {row['passed']}/{row['runs']} runs passed, "
                   f"mean cost {cost} {rel}, "
                   f"mean output tokens {row['mean_output_tokens'] or 'n/a'} -> {verdict}")
        for name, rate in sorted(row["assertions"].items()):
            out.append(f"    {rate:4.0%}  {name}")
    return "\n".join(out)


def main(argv: List[str]) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("results")
    parser.add_argument("--baseline", default=None)
    args = parser.parse_args(argv)
    with open(args.results, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = summarise((data.get("results") or {}).get("results") or [], args.baseline)
    print(render(rows) if rows else "No results found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
