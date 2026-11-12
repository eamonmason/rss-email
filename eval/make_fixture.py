"""Helpers for building eval fixtures (see README "Add a fixture").

    python make_fixture.py text <fixture_dir>      # write digest_text.txt from digest_articles.json
    python make_fixture.py lowrank <src_dir> <dst_dir>
        # copy a fixture, relabelling its must-cover stories to deprioritised sources

``lowrank`` is a deliberate perturbation of a real day, not new data: the stories
and summaries are unchanged, only ``source`` changes. It targets the known failure
mode where a major story from a low-ranked source is dropped under the theme caps.
"""

import json
import os
import shutil
import sys
from typing import Any, Dict, List

LOW_RANK_SOURCES = ["Techmeme", "Slashdot", "Google News"]


def digest_to_text(digest: Dict[str, List[Dict[str, Any]]]) -> str:
    """Render a digest as the readable text the faithfulness grader audits against."""
    lines: List[str] = []
    for category, items in digest.items():
        lines.append(f"## {category}")
        for item in items:
            lines.append(f"- [{item['source']}] {item['title']}")
            lines.append(f"  {item['summary']}")
        lines.append("")
    return "\n".join(lines)


def relabel_to_low_rank(
    digest: Dict[str, List[Dict[str, Any]]], must_cover: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Return a copy of ``digest`` with each must-cover story given a low-rank source.

    Sources rotate through ``LOW_RANK_SOURCES`` in digest order, so the result is
    deterministic. Titles are matched on a normalised substring, like the
    ``major_coverage`` assertion.
    """
    wanted = [title.lower() for title in must_cover]
    out = json.loads(json.dumps(digest))
    n = 0
    for items in out.values():
        for item in items:
            title = item["title"].lower()
            if any(w in title or title in w for w in wanted):
                item["source"] = LOW_RANK_SOURCES[n % len(LOW_RANK_SOURCES)]
                n += 1
    return out


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: List[str]) -> int:
    """CLI entry point."""
    if len(argv) == 3 and argv[1] == "text":
        digest = _load(os.path.join(argv[2], "digest_articles.json"))
        _write(os.path.join(argv[2], "digest_text.txt"), digest_to_text(digest))
        return 0
    if len(argv) == 4 and argv[1] == "lowrank":
        src, dst = argv[2], argv[3]
        os.makedirs(dst, exist_ok=True)
        expected = _load(os.path.join(src, "expected_major.json"))
        digest = relabel_to_low_rank(
            _load(os.path.join(src, "digest_articles.json")), expected["must_cover"]
        )
        _write(os.path.join(dst, "digest_articles.json"), json.dumps(digest, indent=2) + "\n")
        _write(os.path.join(dst, "digest_text.txt"), digest_to_text(digest))
        shutil.copyfile(
            os.path.join(src, "expected_major.json"), os.path.join(dst, "expected_major.json")
        )
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
