"""Helpers for building eval fixtures (see README "Add a fixture").

    python make_fixture.py text <fixture_dir>      # write digest_text.txt from digest_articles.json
    python make_fixture.py lowrank <src_dir> <dst_dir>
        # copy a fixture, relabelling its must-cover stories to deprioritised sources
    python make_fixture.py candidates <batch-metadata.json> [min_feeds]
        # list stories many feeds ran: candidates for a fixture's expected_major.json
    uv run python make_fixture.py refilter <fixture_dir>...
        # drop categories the brief never sees (Other, Science, ...) from existing fixtures
    python make_fixture.py sync-tests
        # rewrite the generated tests block in promptfooconfig.yaml from fixtures/*

``lowrank`` is a deliberate perturbation of a real day, not new data: the stories
and summaries are unchanged, only ``source`` changes. It targets the known failure
mode where a major story from a low-ranked source is dropped under the theme caps.
"""

import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Tuple

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src")

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


def filter_to_brief_input(
    digest: Dict[str, List[Dict[str, Any]]], themed: List[str], personal: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Reduce a digest to what the brief actually receives in production.

    Mirrors ``brief_generator.build_synthesis_input``: only themed then personal categories,
    in configured order, with sponsored articles dropped. Anything else (Other, Science,
    Environment...) never reaches the brief, so a fixture that contains it is not faithful.
    """
    # Imported here so the other subcommands (sync-tests, candidates) keep working with a
    # plain `python`; this one needs the project environment (`uv run python`).
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from rss_email.brief_generator import is_sponsored  # pylint: disable=import-outside-toplevel

    out: Dict[str, List[Dict[str, Any]]] = {}
    for category in list(themed) + list(personal):
        items = [
            item for item in digest.get(category, [])
            if item.get("title") and not is_sponsored(item["title"], item.get("url", ""))
        ]
        if items:
            out[category] = items
    return out


def candidate_stories(meta: Dict[str, Any], min_feeds: int = 3) -> List[Tuple[int, str, List[str]]]:
    """Stored groups covered by at least ``min_feeds`` distinct feeds, most-covered first.

    A story many feeds ran is a defensible "major story" candidate. This only proposes:
    a person picks which ones go in ``expected_major.json``.
    """
    articles = meta["articles"]
    found = []
    for members in meta["groups"]:
        feeds = sorted({articles[i].get("sourceName") or "?" for i in members if i < len(articles)})
        if len(feeds) >= min_feeds:
            found.append((len(feeds), articles[members[0]].get("title", ""), feeds))
    return sorted(found, key=lambda item: (-item[0], item[1]))


FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "promptfooconfig.yaml")
BEGIN_TESTS = "# BEGIN GENERATED TESTS (make_fixture.py sync-tests)"
END_TESTS = "# END GENERATED TESTS"


def render_tests(fixtures_dir: str) -> str:
    """The promptfoo ``tests:`` entries for every fixture directory, in name order.

    Writes ``digest_text.txt`` for a fixture that lacks one. ``expected_major`` is only set when
    the fixture has a curated file.
    """
    lines = []
    for name in sorted(os.listdir(fixtures_dir)):
        folder = os.path.join(fixtures_dir, name)
        if not os.path.isfile(os.path.join(folder, "digest_articles.json")):
            continue
        text_path = os.path.join(folder, "digest_text.txt")
        if not os.path.exists(text_path):
            _write(text_path, digest_to_text(_load(os.path.join(folder, "digest_articles.json"))))
        description = f"{name} digest"
        if name.endswith("-lowrank"):
            description += ", major stories from low-ranked sources"
        lines.append(f"  - description: {description}")
        lines.append("    vars:")
        lines.append(f"      fixture: fixtures/{name}/digest_articles.json")
        if os.path.exists(os.path.join(folder, "expected_major.json")):
            lines.append(f"      expected_major: fixtures/{name}/expected_major.json")
        lines.append(f"      digest_text: file://fixtures/{name}/digest_text.txt")
    return "\n".join(lines)


def sync_tests(config_path: str = CONFIG_PATH, fixtures_dir: str = FIXTURES_DIR) -> bool:
    """Rewrite the generated block of ``config_path``. Returns True if the file changed."""
    with open(config_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    pattern = re.compile(re.escape(BEGIN_TESTS) + r".*?" + re.escape(END_TESTS), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"{config_path} has no '{BEGIN_TESTS}' ... '{END_TESTS}' block")
    block = f"{BEGIN_TESTS}\n{render_tests(fixtures_dir)}\n{END_TESTS}"
    updated = pattern.sub(lambda _: block, text)
    if updated != text:
        _write(config_path, updated)
    return updated != text


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
    if len(argv) in (3, 4) and argv[1] == "candidates":
        min_feeds = int(argv[3]) if len(argv) == 4 else 3
        found = candidate_stories(_load(argv[2]), min_feeds)
        for count, title, feeds in found:
            print(f"{count} feeds | {title} | {', '.join(feeds)}")
        if not found:
            print(f"no story was covered by {min_feeds}+ feeds; try a lower number")
        return 0
    if len(argv) >= 3 and argv[1] == "refilter":
        if _SRC not in sys.path:
            sys.path.insert(0, _SRC)
        from rss_email.brief_generator import load_brief_config  # pylint: disable=import-outside-toplevel
        config = load_brief_config()
        for folder in argv[2:]:
            path = os.path.join(folder, "digest_articles.json")
            digest = filter_to_brief_input(
                _load(path), config.get("themed_categories", []), config.get("personal_categories", [])
            )
            _write(path, json.dumps(digest, indent=2) + "\n")
            _write(os.path.join(folder, "digest_text.txt"), digest_to_text(digest))
            print(f"{folder}: {sum(len(v) for v in digest.values())} articles in {sorted(digest)}")
        return 0
    if len(argv) == 2 and argv[1] == "sync-tests":
        print("updated" if sync_tests() else "already up to date")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
