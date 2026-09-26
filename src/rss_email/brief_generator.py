"""Generate the companion "RSS Brief" email from categorised articles.

The brief distils a day's categorised RSS articles into themes, signal ratings,
and reader-specific relevance using a single Claude call, then renders an
email-safe HTML message that pairs with the daily digest. It is generic and
reusable: the reader profile and category configuration live in
``brief_config.json`` (with optional environment overrides), not in code.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.parse
from importlib.resources import files
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Set

import anthropic
import pydantic

from .article_processor import get_anthropic_api_key
from .brief_prompt import (
    FIDELITY_RULE,
    MAJOR_STORY_RULE,
    PREVIOUS_CONTEXT_RULE,
    PROMPT_TEMPLATE,
    RUTHLESS_RULE,
    SOURCE_RULE,
)
from .email_articles import category_color
from .json_utils import extract_json_from_text
from .models import (
    BriefCategory,
    BriefSynthesis,
    BriefTheme,
    CrossCuttingSignal,
    MustRead,
    PersonalBlock,
)
from .url_utils import normalise_link, strip_credential_params

logger = logging.getLogger(__name__)

DEFAULT_SYNTHESIS_MODEL = "claude-sonnet-4-6"
SYNTHESIS_MAX_TOKENS = 8192
WORD_OVERLAP_THRESHOLD = 0.75

# Length caps: stated in the prompt, then enforced on the parsed synthesis
# (``_enforce_caps``) so prompt drift can't bring back a long brief.
DEFAULT_CAPS = {
    "must_read_min": 5,
    "must_read_max": 8,
    "max_themes_per_category": 3,
    "max_total_themes": 12,
    "cross_cutting_max": 3,
}

# URL path segments / title prefixes that mark paid or advertorial content.
SPONSORED_PATH_MARKERS = ("/sponsored/", "/partner-content/", "/paid-post/", "/brandvoice/")
SPONSORED_TITLE_RE = re.compile(r"^\W*(sponsored|advertorial|paid post)\b", re.IGNORECASE)

# Signal badge styling: (background, border, text) hexes, consistent with the
# digest's category palette.
SIGNAL_BADGE_STYLES = {
    "HIGH": ("#fdecea", "#f44336", "#b71c1c"),
    "STRATEGIC": ("#fff8e1", "#ff9800", "#e65100"),
    "GENERAL": ("#eafaf1", "#4caf50", "#1b5e20"),
}

# Source-tier ordering used to present higher-quality sources to the model first.
_TIER_ORDER = {"high": 0, "medium": 1, "low": 2}


def _render_previous_context_section(previous_context: str) -> str:
    """Wrap rendered brief memory with usage rules, or omit the section entirely."""
    if not previous_context:
        return ""
    return (
        f"PREVIOUS DAYS (for context only - what this reader was already told):\n"
        f"{previous_context}\n\n{PREVIOUS_CONTEXT_RULE}\n"
    )


def load_brief_config() -> Dict[str, Any]:
    """Load ``brief_config.json`` and apply environment-variable overrides."""
    config: Dict[str, Any] = {}
    try:
        raw = files("rss_email").joinpath("brief_config.json").read_text(encoding="utf-8")
        config = json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not load brief_config.json (%s); using defaults", exc)

    config.setdefault("enabled", True)
    config.setdefault("model", DEFAULT_SYNTHESIS_MODEL)
    config.setdefault("reader_profile", "")
    config.setdefault("personal_interests", "")
    config.setdefault("major_story_floor", True)
    config.setdefault("themed_categories", [])
    config.setdefault("personal_categories", [])
    config.setdefault("prioritised_sources", [])
    config.setdefault("deprioritised_sources", [])
    for key, value in DEFAULT_CAPS.items():
        config.setdefault(key, value)

    if "BRIEF_ENABLED" in os.environ:
        config["enabled"] = os.environ["BRIEF_ENABLED"].lower() == "true"
    if os.environ.get("BRIEF_CLAUDE_MODEL"):
        config["model"] = os.environ["BRIEF_CLAUDE_MODEL"]
    if os.environ.get("BRIEF_READER_PROFILE"):
        config["reader_profile"] = os.environ["BRIEF_READER_PROFILE"]

    return config


def _article_field(article: Any, name: str) -> Any:
    """Read a field from an article that may be a dict or an object."""
    if isinstance(article, dict):
        return article.get(name)
    return getattr(article, name, None)


def _article_source(article: Any) -> str:
    """Best-effort feed/source name for an article (``ProcessedArticle`` or dict)."""
    sources = _article_field(article, "sources")
    if sources:
        first = sources[0]
        name = (
            first.get("feed_name")
            if isinstance(first, dict)
            else getattr(first, "feed_name", None)
        )
        if name:
            return str(name)
    if isinstance(article, dict):
        return str(article.get("sourceName") or "")
    return str(getattr(article, "source_name", "") or "")


def source_tier(name: str, config: Dict[str, Any]) -> str:
    """Classify a source name as ``high``, ``low``, or ``medium`` priority.

    Independent blogs, Hacker News, Reddit, etc. (``prioritised_sources``) rank
    ``high``; wire/aggregator reposts (``deprioritised_sources``) rank ``low``.
    """
    if not name:
        return "medium"
    lowered = name.lower()
    for token in config.get("prioritised_sources", []):
        if token and token.lower() in lowered:
            return "high"
    for token in config.get("deprioritised_sources", []):
        if token and token.lower() in lowered:
            return "low"
    return "medium"


def ensure_article_ids(
    synthesis_input: Dict[str, List[Dict[str, str]]]
) -> Dict[str, List[Dict[str, str]]]:
    """Assign a stable numeric string ``id`` to every article that lacks one.

    Ids are assigned by walking ``synthesis_input`` in plain dict/list order
    (categories in dict-iteration order, articles in each category's list
    order), counting from 1 - there is no dependency on
    ``themed_categories``/``personal_categories`` config order, so this is
    safe to call both on output freshly built by ``build_synthesis_input``
    and on a ``synthesis_input``-shaped dict loaded straight from disk (e.g.
    an eval fixture predating the ``id`` field). Existing ids are left
    untouched, so repeated calls are idempotent. Mutates in place and returns
    the input for convenience.
    """
    counter = 0
    for items in synthesis_input.values():
        for item in items:
            counter += 1
            item.setdefault("id", str(counter))
    return synthesis_input


@pydantic.validate_call(
    config={"arbitrary_types_allowed": True}, validate_return=True
)
def build_synthesis_input(
    categories: Dict[str, List[Any]],
    themed: List[str],
    personal: List[str],
    seen_links: Optional[Set[str]] = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Reduce categorised articles to ``{category: [{id, title, url, summary}]}``.

    ``comments`` (the discussion-thread URL, where the feed had one) rides
    along too - like ``url`` it never enters the prompt, only the index.

    Only themed and personal categories are kept; everything else is dropped to
    keep the brief tight. Also dropped: sponsored/advertorial articles
    (``is_sponsored``) and articles whose ``normalise_link`` key is in
    ``seen_links`` (already featured by a previous day's brief - see
    ``brief_memory.seen_links``). Credential query params are stripped from
    ``url``/``comments``. Accepts ``ProcessedArticle`` objects or raw dicts.
    """
    seen = seen_links or set()
    dropped_sponsored = dropped_seen = 0
    synthesis_input: Dict[str, List[Dict[str, str]]] = {}
    for category in list(themed) + list(personal):
        articles = categories.get(category)
        if not articles:
            continue
        items: List[Dict[str, str]] = []
        for article in articles:
            title = _article_field(article, "title")
            if not title:
                continue
            url = strip_credential_params(str(_article_field(article, "link") or ""))
            if is_sponsored(str(title), url):
                dropped_sponsored += 1
                continue
            if seen and normalise_link(url) in seen:
                dropped_seen += 1
                continue
            items.append(
                {
                    "title": str(title),
                    "url": url,
                    "summary": str(_article_field(article, "summary") or ""),
                    "source": _article_source(article),
                    "comments": strip_credential_params(
                        str(_article_field(article, "comments") or "")
                    ),
                }
            )
        if items:
            synthesis_input[category] = items
    if dropped_sponsored or dropped_seen:
        logger.info(
            "Brief input: dropped %d sponsored and %d previously-featured articles",
            dropped_sponsored,
            dropped_seen,
        )
    return ensure_article_ids(synthesis_input)


def is_sponsored(title: str, url: str) -> bool:
    """True when the URL path or title marks the article as paid content."""
    path = urllib.parse.urlsplit(url).path.lower() if url else ""
    if any(marker in path + "/" for marker in SPONSORED_PATH_MARKERS):
        return True
    return bool(SPONSORED_TITLE_RE.match(title or ""))


@pydantic.validate_call(validate_return=True, config={"arbitrary_types_allowed": True})
def build_prompt(
    synthesis_input: Dict[str, List[Dict[str, str]]],
    config: Dict[str, Any],
    previous_context: str = "",
    date: str = "",
) -> str:
    """Assemble the synthesis prompt from the profile, sources, and articles.

    Within each category, articles are ordered by source tier (high-quality
    sources first) so the model sees prioritised sources before the rest.
    ``previous_context`` is the rendered output of
    ``brief_memory.render_previous_context`` - recent days' themes, given as
    background so Claude can avoid repeating stories and frame developing
    ones as continuations. Empty by default, which omits the section.
    ``date`` is today's date (``YYYY-MM-DD``) so the model can judge whether
    an event has already happened.
    """
    blocks = []
    for category, items in synthesis_input.items():
        ranked = sorted(
            items,
            key=lambda item: _TIER_ORDER[source_tier(item.get("source", ""), config)],
        )
        lines = [f"## {category}"]
        for item in ranked:
            source = item.get("source") or "Unknown"
            lines.append(f"- ({item['id']}) [{source}] {item['title']}")
            summary = item.get("summary")
            if summary:
                lines.append(f"  {summary}")
        blocks.append("\n".join(lines))
    articles_block = "\n\n".join(blocks)
    major_rule = MAJOR_STORY_RULE if config.get("major_story_floor", True) else RUTHLESS_RULE
    prompt = PROMPT_TEMPLATE
    for key, default in DEFAULT_CAPS.items():
        prompt = prompt.replace("{" + key.upper() + "}", str(config.get(key, default)))
    return (
        prompt
        .replace("{DATE}", date or "unknown")
        .replace("{READER_PROFILE}", config.get("reader_profile", ""))
        .replace("{PERSONAL_INTERESTS}", config.get("personal_interests", ""))
        .replace("{MAJOR_STORY_RULE}", major_rule)
        .replace("{SOURCE_RULE}", SOURCE_RULE)
        .replace("{FIDELITY_RULE}", FIDELITY_RULE)
        .replace("{PREVIOUS_CONTEXT}", _render_previous_context_section(previous_context))
        .replace("{ARTICLES_BY_CATEGORY}", articles_block)
    )


def _normalise_key(text: str) -> str:
    """Reduce a category key to comparable alphanumerics (so AI/ML == AI_ML)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _canonical_category(key: str, known: List[str]) -> str:
    """Map a model-returned category key onto its configured name when they match.

    Models often sanitise ``AI/ML`` to ``AI_ML``; this restores the configured
    spelling so ordering and the rendered header are correct. Unknown keys are
    returned unchanged.
    """
    if not known or key in known:
        return key
    target = _normalise_key(key)
    for candidate in known:
        if _normalise_key(candidate) == target:
            return candidate
    return key


def _parse_synthesis(
    response_text: str, known_categories: Optional[List[str]] = None
) -> Optional[BriefSynthesis]:
    """Parse and validate a synthesis response into a ``BriefSynthesis``."""
    data = extract_json_from_text(response_text)
    if not data:
        return None
    payload = dict(data)
    cross_cutting = payload.pop("cross_cutting", []) or []
    personal = payload.pop("personal", None)
    must_read = payload.pop("must_read", []) or []
    known = known_categories or []
    categories = {
        _canonical_category(key, known): value for key, value in payload.items()
    }
    try:
        return BriefSynthesis(
            must_read=must_read,
            categories=categories,
            cross_cutting=cross_cutting,
            personal=personal,
        )
    except pydantic.ValidationError as exc:
        logger.warning("Brief synthesis failed schema validation: %s", exc)
        return None


def _enforce_caps(brief: BriefSynthesis, config: Dict[str, Any]) -> BriefSynthesis:
    """Truncate the synthesis to the configured length caps, preserving order.

    Themes are capped per category, then in total (earlier categories in the
    model's output win). Categories left with no themes are dropped.
    """
    def cap(key: str) -> int:
        return int(config.get(key, DEFAULT_CAPS[key]))

    per_category = cap("max_themes_per_category")
    remaining = cap("max_total_themes")
    categories: Dict[str, BriefCategory] = {}
    for name, category in brief.categories.items():
        themes = category.themes[: min(per_category, max(remaining, 0))]
        remaining -= len(themes)
        if themes:
            categories[name] = category.model_copy(update={"themes": themes})
    return brief.model_copy(
        update={
            "must_read": brief.must_read[: cap("must_read_max")],
            "categories": categories,
            "cross_cutting": brief.cross_cutting[: cap("cross_cutting_max")],
        }
    )


def synthesize(
    synthesis_input: Dict[str, List[Dict[str, str]]],
    config: Dict[str, Any],
    client: Optional[Any] = None,
    previous_context: str = "",
    date: str = "",
) -> Optional[BriefSynthesis]:
    """Run one Claude synthesis call (with one retry) and validate the result.

    The model, reader profile, personal interests, source tiers, and category
    names are read from ``config``. Returns ``None`` on persistent failure so the
    caller can skip the brief without blocking the main digest. Never raises.

    Assigns article ids via ``ensure_article_ids`` if ``synthesis_input``
    doesn't already have them (mutates it in place) - ``build_prompt`` cites
    articles by id, so this guarantees it always has one to cite regardless
    of how the caller built ``synthesis_input``.

    ``previous_context`` and ``date`` are passed straight through to
    ``build_prompt`` - see its docstring. The parsed result is truncated to
    the configured caps by ``_enforce_caps``.
    """
    if not synthesis_input:
        logger.info("No themed articles to synthesise; skipping brief")
        return None

    ensure_article_ids(synthesis_input)

    if client is None:
        client = anthropic.Anthropic(api_key=get_anthropic_api_key())

    model = config.get("model", DEFAULT_SYNTHESIS_MODEL)
    known_categories = list(config.get("themed_categories", [])) + list(
        config.get("personal_categories", [])
    )
    prompt = build_prompt(synthesis_input, config, previous_context, date)
    api_timeout = int(os.environ.get("CLAUDE_API_TIMEOUT", "120"))

    for attempt in (1, 2):
        try:
            with client.messages.stream(
                model=model,
                max_tokens=SYNTHESIS_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                timeout=api_timeout,
            ) as stream:
                response_text = stream.get_final_text().strip()
            brief = _parse_synthesis(response_text, known_categories)
        except (
            anthropic.APIError,
            anthropic.APIConnectionError,
            pydantic.ValidationError,
            ValueError,
            IndexError,
            AttributeError,
            TypeError,
        ) as exc:
            logger.error("Brief synthesis call failed (attempt %d): %s", attempt, exc)
            continue

        if brief is not None:
            return _enforce_caps(brief, config)
        logger.warning(
            "Brief synthesis JSON parse/validation failed (attempt %d)", attempt
        )

    logger.error("Brief synthesis failed after retry; skipping brief for this run")
    return None


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for title matching."""
    return re.sub(r"\s+", " ", text).strip().lower()


@pydantic.validate_call(validate_return=True)
def build_article_index(
    synthesis_input: Dict[str, List[Dict[str, str]]]
) -> Dict[str, Dict[str, str]]:
    """Build an ``id -> {title, url, source, comments}`` index from the input.

    Every article is included, even ones with an empty ``url`` - the renderer
    needs ``title``/``source`` for those too, to compose a correct plain-text
    (unlinked) citation. Requires ``ensure_article_ids`` to have been called
    on ``synthesis_input`` first (``build_synthesis_input`` does this).
    """
    index: Dict[str, Dict[str, str]] = {}
    for items in synthesis_input.values():
        for item in items:
            article_id = item.get("id")
            if not article_id:
                continue
            index[article_id] = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "comments": item.get("comments", ""),
            }
    return index


def _best_title_match(text: str, candidates: List[str]) -> Optional[str]:
    """Return the candidate title that best matches ``text``.

    Exact match -> normalised/lowercased -> word-overlap (Jaccard) >= 0.75 ->
    ``None`` (no confident match).
    """
    if not text:
        return None
    if text in candidates:
        return text

    target = _normalise(text)
    normalised = {_normalise(candidate): candidate for candidate in candidates}
    if target in normalised:
        return normalised[target]

    target_words = set(target.split())
    if target_words:
        best_candidate: Optional[str] = None
        best_score = 0.0
        for candidate in candidates:
            words = set(_normalise(candidate).split())
            if not words:
                continue
            overlap = len(target_words & words) / len(target_words | words)
            if overlap > best_score:
                best_score = overlap
                best_candidate = candidate
        if best_score >= WORD_OVERLAP_THRESHOLD:
            return best_candidate
    return None


@pydantic.validate_call(validate_return=True)
def match_title_to_url(title: str, article_map: Dict[str, str]) -> Optional[str]:
    """Resolve an article title to a URL via ``_best_title_match``."""
    match = _best_title_match(title, list(article_map.keys()))
    return article_map.get(match) if match else None


def _signal_badge(signal: str) -> str:
    """Render a signal-strength badge as an inline-styled span."""
    background, border, text = SIGNAL_BADGE_STYLES.get(
        signal, SIGNAL_BADGE_STYLES["GENERAL"]
    )
    return (
        f'<span style="display: inline-block; padding: 2px 8px; font-size: 0.75em; '
        f'font-weight: bold; border-radius: 4px; background-color: {background}; '
        f'border: 1px solid {border}; color: {text};">{html.escape(signal)}</span>'
    )


def _discussion_suffix(comments: Optional[str], url: Optional[str]) -> str:
    """Render the trailing " - discussion" link for an article citation.

    Empty when the feed exposed no thread, or when the thread *is* the article
    link (Reddit self-posts, Slashdot) - a link to itself is just noise.
    """
    if not comments or comments == url:
        return ""
    return (
        '<span style="color: #888;"> &middot; </span>'
        f'<a href="{html.escape(comments)}" target="_blank" '
        'style="color: #888; text-decoration: underline; font-size: 0.9em;">'
        "discussion</a>"
    )


def _resolve_reference(
    ref: str, article_index: Dict[str, Dict[str, str]]
) -> Optional[Dict[str, str]]:
    """Resolve a cited id (or, as a fallback, title-like text) to its index entry."""
    entry = article_index.get(ref)
    if entry is None:
        title_lookup = {
            item["title"]: item for item in article_index.values() if item.get("title")
        }
        match = _best_title_match(ref, list(title_lookup.keys()))
        entry = title_lookup.get(match) if match else None
    if entry is None:
        logger.warning("Brief cited an unresolvable article reference %r; dropping", ref)
    return entry


def _article_link_html(entry: Dict[str, str]) -> str:
    """Render one article as "[Source] Title" (linked when it has a URL) + discussion."""
    title = entry.get("title", "")
    source = entry.get("source", "")
    display = html.escape(f"[{source}] {title}" if source else title)
    url = entry.get("url")
    suffix = _discussion_suffix(entry.get("comments"), url)
    if url:
        return (
            f'<a href="{html.escape(url)}" target="_blank" '
            f'style="color: #0066cc; text-decoration: underline;">'
            f"{display}</a>{suffix}"
        )
    return f'<span style="color: #555;">{display}</span>{suffix}'


def _entry_key(entry: Dict[str, str]) -> str:
    """Identity of an index entry for de-duplicating citations."""
    return normalise_link(entry.get("url", "")) or entry.get("title", "")


def _render_article_links(
    references: List[str],
    article_index: Dict[str, Dict[str, str]],
    shown: Optional[Set[str]] = None,
) -> str:
    """Render a list of article citations (ids) as links, plain text if unlinked.

    ``references`` are expected to be the numeric ids Claude was asked to cite.
    If a reference isn't a known id (Claude ignored the instruction and
    returned title-like text instead), fall back to fuzzy-matching it against
    known titles. Either way, the *displayed* text is always composed by
    Python from the matched entry's own ``source``/``title`` fields - never
    Claude's raw string - so a malformed bracket in Claude's output can never
    reach the email. A reference that resolves neither way is dropped.

    ``shown`` (mutated) holds the keys of articles already listed elsewhere in
    the brief; an article already in it is skipped, so the same article is
    never listed under two themes.
    """
    parts = []
    for ref in references:
        entry = _resolve_reference(ref, article_index)
        if entry is None:
            continue
        if shown is not None:
            key = _entry_key(entry)
            if key in shown:
                continue
            shown.add(key)
        parts.append(f'<li style="margin: 0 0 6px 0;">{_article_link_html(entry)}</li>')
    if not parts:
        return ""
    return (
        '<ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 0.875em;">'
        + "".join(parts)
        + "</ul>"
    )


# "(article N[, M ...])" is unambiguous and always resolved-or-stripped. A bare
# "(N)" is ambiguous (may be an ordinary parenthetical number in prose), so it's
# only touched when N resolves to a known article id - see
# _linkify_or_strip_citations.
_ARTICLE_CITATION_RE = re.compile(
    r"\(\s*articles?\s*#?\s*(?P<ids>\d+(?:\s*(?:,|and|&)\s*#?\d+)*)\s*\)"
    r"|\(\s*(?P<bare_id>\d{1,4})\s*\)",
    re.IGNORECASE,
)
_CITATION_ID_RE = re.compile(r"\d+")


def _linkify_or_strip_citations(text: str, article_index: Dict[str, Dict[str, str]]) -> str:
    """Escape ``text`` for HTML while resolving/stripping inline citations.

    Claude is instructed (``PROMPT_TEMPLATE``) to cite articles only inside the
    top_articles/top_stories JSON fields, never inline in prose - but as
    defense in depth (matching the ``a5c7dcc`` principle of never trusting
    Claude's raw string for a citation), this scans free-text brief fields for
    citation-like parentheticals before escaping. A resolvable id becomes a
    real inline link whose display text (the source name) is composed by
    Python, never echoed from Claude's string; an unresolvable one is dropped.
    A drop-in replacement for a bare ``html.escape(text)`` call on any brief
    prose field.
    """
    if not text:
        return html.escape(text)

    pieces: List[str] = []
    last_end = 0
    for match in _ARTICLE_CITATION_RE.finditer(text):
        ids_group = match.group("ids")
        if ids_group is not None:
            candidate_ids = _CITATION_ID_RE.findall(ids_group)
        else:
            candidate_ids = [match.group("bare_id")]
            if candidate_ids[0] not in article_index:
                continue  # ambiguous bare "(N)" that isn't a known id - leave untouched

        links = []
        for article_id in candidate_ids:
            entry = article_index.get(article_id)
            if entry and entry.get("url"):
                source = entry.get("source") or "source"
                links.append(
                    f'<a href="{html.escape(entry["url"])}" target="_blank" '
                    f'style="color: #0066cc; text-decoration: underline;">'
                    f"{html.escape(source)}</a>"
                )
            elif not entry:
                logger.warning(
                    "Brief prose cited an unresolvable article reference %r; dropping",
                    article_id,
                )

        pieces.append(html.escape(text[last_end:match.start()]))
        pieces.append("(" + ", ".join(links) + ")" if links else "")
        last_end = match.end()

    pieces.append(html.escape(text[last_end:]))
    result = "".join(pieces)
    result = re.sub(r"\s{2,}", " ", result)
    result = re.sub(r"\s+([.,;:!?])", r"\1", result)
    return result.strip()


def _render_theme(
    theme: BriefTheme,
    article_index: Dict[str, Dict[str, str]],
    shown: Optional[Set[str]] = None,
) -> str:
    """Render a single theme: badge, name, tldr, relevance, linked articles."""
    parts = [
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 18px 0;"><tr><td '
        'style="padding: 14px 16px; background-color: #f8f9fa; '
        'border-left: 4px solid #3498db;">',
        f'<p style="margin: 0 0 8px 0; font-size: 1em; color: #2c3e50;">'
        f"{_signal_badge(theme.signal_strength)} "
        f"<strong>{html.escape(theme.theme)}</strong></p>",
        f'<p style="margin: 0 0 10px 0; font-size: 1em; color: #555; '
        f'line-height: 1.6;">{_linkify_or_strip_citations(theme.tldr, article_index)}</p>',
    ]
    if theme.relevance_to_reader:
        parts.append(
            f'<p style="margin: 0 0 8px 0; font-size: 0.875em; color: #1a5276; '
            f'line-height: 1.5;"><strong>Why this matters to you:</strong> '
            f"{_linkify_or_strip_citations(theme.relevance_to_reader, article_index)}</p>"
        )
    parts.append(_render_article_links(theme.top_articles, article_index, shown))
    parts.append("</td></tr></table>")
    return "".join(parts)


def _render_category(
    name: str,
    category: BriefCategory,
    article_index: Dict[str, Dict[str, str]],
    shown: Optional[Set[str]] = None,
) -> str:
    """Render a themed category: coloured header, then themes.

    ``week_verdict`` is no longer requested or rendered (it added a line per
    category without telling the reader what to read); the model field stays
    for backward compatibility.
    """
    header = (
        f'<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        f'style="background-color: {category_color(name)}; border-radius: 6px; '
        f'margin: 0 0 12px 0;"><tr><td>'
        f'<h2 style="color: #ffffff; margin: 0; font-size: 1.25em; '
        f'font-weight: bold; line-height: 1.3;">{html.escape(name)}</h2>'
        f"</td></tr></table>"
    )
    themes = "".join(
        _render_theme(theme, article_index, shown) for theme in category.themes
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + themes + "</td></tr></table>"
    )


def _render_cross_cutting(
    signals: List[CrossCuttingSignal], article_index: Dict[str, Dict[str, str]]
) -> str:
    """Render the cross-cutting signals section."""
    if not signals:
        return ""
    rows = []
    for signal in signals:
        cats = ", ".join(html.escape(cat) for cat in signal.categories_involved)
        rows.append(
            '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="margin: 0 0 14px 0;"><tr><td '
            'style="padding: 14px 16px; background-color: #f8f9fa; '
            'border-left: 4px solid #667eea;">'
            f'<p style="margin: 0 0 6px 0; font-size: 1em; color: #2c3e50;">'
            f"<strong>{html.escape(signal.signal)}</strong></p>"
            f'<p style="margin: 0 0 6px 0; font-size: 0.8em; color: #666;">{cats}</p>'
            f'<p style="margin: 0; font-size: 1em; color: #555; '
            f'line-height: 1.6;">'
            f"{_linkify_or_strip_citations(signal.implication, article_index)}</p>"
            "</td></tr></table>"
        )
    header = (
        '<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        'style="background-color: #667eea; border-radius: 6px; '
        'margin: 0 0 12px 0;"><tr><td>'
        '<h2 style="color: #ffffff; margin: 0; font-size: 1.25em; '
        'font-weight: bold;">Cross-Cutting Signals</h2></td></tr></table>'
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + "".join(rows) + "</td></tr></table>"
    )


def _render_personal(
    personal: Optional[PersonalBlock],
    article_index: Dict[str, Dict[str, str]],
    shown: Optional[Set[str]] = None,
) -> str:
    """Render the personal-interest digest block (e.g. Cycling)."""
    if personal is None:
        return ""
    header = (
        '<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        'style="background-color: #16a085; border-radius: 6px; '
        'margin: 0 0 12px 0;"><tr><td>'
        '<h2 style="color: #ffffff; margin: 0; font-size: 1.25em; '
        'font-weight: bold;">Personal</h2></td></tr></table>'
    )
    summary = ""
    if personal.summary:
        summary = (
            f'<p style="margin: 0 0 10px 0; font-size: 1em; color: #555; '
            f'line-height: 1.6;">'
            f"{_linkify_or_strip_citations(personal.summary, article_index)}</p>"
        )
    links = _render_article_links(personal.top_stories, article_index, shown)
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + summary + links + "</td></tr></table>"
    )


def _render_must_read(
    items: Iterable[MustRead], article_index: Dict[str, Dict[str, str]]
) -> str:
    """Render the "Read these" list: numbered articles, each with why to read it."""
    rows = []
    seen: Set[str] = set()
    for item in items:
        entry = _resolve_reference(item.id, article_index)
        if entry is None or _entry_key(entry) in seen:
            continue
        seen.add(_entry_key(entry))
        why = ""
        if item.why:
            why = (
                '<br><span style="color: #555; font-size: 0.9em; line-height: 1.5;">'
                f"{_linkify_or_strip_citations(item.why, article_index)}</span>"
            )
        rows.append(
            f'<li style="margin: 0 0 12px 0; line-height: 1.5;">'
            f"{_article_link_html(entry)}{why}</li>"
        )
    if not rows:
        return ""
    header = (
        '<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        'style="background-color: #2c3e50; border-radius: 6px; '
        'margin: 0 0 12px 0;"><tr><td>'
        '<h2 style="color: #ffffff; margin: 0; font-size: 1.25em; '
        'font-weight: bold;">Read these</h2></td></tr></table>'
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header
        + '<ol style="margin: 0; padding-left: 24px; font-size: 1em;">'
        + "".join(rows) + "</ol></td></tr></table>"
    )


@pydantic.validate_call(validate_return=True)
def render_brief_html(
    brief: BriefSynthesis,
    article_index: Dict[str, Dict[str, str]],
    date: str,
    article_count: int,
    themed_order: Optional[List[str]] = None,
) -> str:
    """Render the validated synthesis into an email-safe HTML body."""
    ordered: List[str] = []
    for name in list(themed_order or []) + list(brief.categories.keys()):
        if name in brief.categories and name not in ordered:
            ordered.append(name)

    shown: Set[str] = set()
    sections = [_render_must_read(brief.must_read, article_index)]
    sections.extend(
        _render_category(name, brief.categories[name], article_index, shown)
        for name in ordered
    )
    sections.append(_render_cross_cutting(brief.cross_cutting, article_index))
    sections.append(_render_personal(brief.personal, article_index, shown))
    brief_content = "\n".join(section for section in sections if section)

    template = files("rss_email").joinpath("brief_body.html").read_text(encoding="utf-8")
    return template.format(
        subject=f"RSS Brief — {date}",
        article_count=article_count,
        brief_content=brief_content,
    )


class BriefResult(NamedTuple):
    """Everything ``_maybe_send_brief`` needs to send the email and update memory."""

    html: str
    synthesis: BriefSynthesis
    article_index: Dict[str, Dict[str, str]]


def generate_brief_full(
    categories: Dict[str, List[Any]],
    *,
    date: str,
    article_count: int,
    client: Optional[Any] = None,
    previous_context: str = "",
    seen_links: Optional[Set[str]] = None,
) -> Optional[BriefResult]:
    """Build the RSS Brief and return its HTML plus the validated synthesis.

    Returns ``None`` if the brief is disabled, there is no themed/personal
    content, or synthesis failed. Never raises. The returned ``synthesis``
    and ``article_index`` are what ``brief_memory.build_day_record`` needs
    to persist today's themes for future runs - use ``generate_brief``
    instead when only the HTML is needed. ``seen_links`` (from
    ``brief_memory.seen_links``) drops articles a previous brief featured.
    """
    config = load_brief_config()
    if not config.get("enabled", True):
        logger.info("RSS Brief is disabled; skipping")
        return None

    synthesis_input = build_synthesis_input(
        categories,
        config.get("themed_categories", []),
        config.get("personal_categories", []),
        seen_links=seen_links,
    )
    if not synthesis_input:
        logger.info("No themed/personal articles available; skipping brief")
        return None

    brief = synthesize(
        synthesis_input,
        config,
        client=client,
        previous_context=previous_context,
        date=date,
    )
    if brief is None:
        return None

    article_index = build_article_index(synthesis_input)
    rendered_html = render_brief_html(
        brief,
        article_index,
        date,
        article_count,
        themed_order=config.get("themed_categories", []),
    )
    return BriefResult(html=rendered_html, synthesis=brief, article_index=article_index)


def generate_brief(
    categories: Dict[str, List[Any]],
    *,
    date: str,
    article_count: int,
    client: Optional[Any] = None,
    previous_context: str = "",
    seen_links: Optional[Set[str]] = None,
) -> Optional[str]:
    """Build and render the RSS Brief email body.

    Returns the HTML body, or ``None`` if the brief is disabled, there is no
    themed/personal content, or synthesis failed. Never raises. A thin
    HTML-only wrapper around ``generate_brief_full``.
    """
    result = generate_brief_full(
        categories,
        date=date,
        article_count=article_count,
        client=client,
        previous_context=previous_context,
        seen_links=seen_links,
    )
    return result.html if result else None
