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
from importlib.resources import files
from typing import Any, Dict, List, Optional

import anthropic
import pydantic

from .article_processor import get_anthropic_api_key
from .email_articles import category_color
from .json_utils import extract_json_from_text
from .models import (
    BriefCategory,
    BriefSynthesis,
    BriefTheme,
    CrossCuttingSignal,
    PersonalBlock,
)

logger = logging.getLogger(__name__)

DEFAULT_SYNTHESIS_MODEL = "claude-sonnet-4-6"
SYNTHESIS_MAX_TOKENS = 8192
WORD_OVERLAP_THRESHOLD = 0.75

# Signal badge styling: (background, border, text) hexes, consistent with the
# digest's category palette.
SIGNAL_BADGE_STYLES = {
    "HIGH": ("#fdecea", "#f44336", "#b71c1c"),
    "STRATEGIC": ("#fff8e1", "#ff9800", "#e65100"),
    "GENERAL": ("#eafaf1", "#4caf50", "#1b5e20"),
}

# Note: ``{READER_PROFILE}`` and ``{ARTICLES_BY_CATEGORY}`` are substituted with
# ``str.replace`` (not ``str.format``) so the literal JSON braces below survive.
PROMPT_TEMPLATE = """You are synthesising a day's RSS digest articles for a specific reader.

READER PROFILE:
{READER_PROFILE}

For each professional category below, identify 3-5 key themes. Return ONLY valid
JSON, no markdown, no backticks, matching this schema:

{
  "<CATEGORY_KEY>": {
    "week_verdict": "one crisp sentence on what this category's day signals",
    "themes": [
      {
        "theme": "5-8 words",
        "signal_strength": "HIGH | STRATEGIC | GENERAL",
        "tldr": "2-3 sentences",
        "top_articles": ["exact article title 1", "title 2", "title 3"],
        "relevance_to_reader": "one sentence tied to the reader profile, or null"
      }
    ]
  },
  ... one object per category ...,
  "cross_cutting": [
    { "signal": "...", "categories_involved": ["c1","c2"], "implication": "..." }
  ],
  "personal": { "top_stories": ["title 1","title 2","title 3"], "summary": "1-2 sentences" }
}

Signal strength:
- HIGH      = paradigm shift, affects the reader's decisions now
- STRATEGIC = watch-list item, 6-18 month horizon
- GENERAL   = awareness only, no action

Be ruthless with noise: ignore incremental patch notes, repetitive market
commentary, and minor funding rounds. Use the reader's exact article titles in
top_articles so they can be linked. relevance_to_reader must be null when a theme
has no real bearing on the profile - do not invent relevance.

ARTICLES:
{ARTICLES_BY_CATEGORY}
"""


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
    config.setdefault("themed_categories", [])
    config.setdefault("personal_categories", [])

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


@pydantic.validate_call(
    config={"arbitrary_types_allowed": True}, validate_return=True
)
def build_synthesis_input(
    categories: Dict[str, List[Any]],
    themed: List[str],
    personal: List[str],
) -> Dict[str, List[Dict[str, str]]]:
    """Reduce categorised articles to ``{category: [{title, url, summary}]}``.

    Only themed and personal categories are kept; everything else is dropped to
    keep the brief tight. Accepts ``ProcessedArticle`` objects or raw dicts.
    """
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
            items.append(
                {
                    "title": str(title),
                    "url": str(_article_field(article, "link") or ""),
                    "summary": str(_article_field(article, "summary") or ""),
                }
            )
        if items:
            synthesis_input[category] = items
    return synthesis_input


@pydantic.validate_call(validate_return=True)
def build_prompt(
    synthesis_input: Dict[str, List[Dict[str, str]]], reader_profile: str
) -> str:
    """Assemble the synthesis prompt from the reader profile and articles."""
    blocks = []
    for category, items in synthesis_input.items():
        lines = [f"## {category}"]
        for item in items:
            lines.append(f"- {item['title']}")
            summary = item.get("summary")
            if summary:
                lines.append(f"  {summary}")
        blocks.append("\n".join(lines))
    articles_block = "\n\n".join(blocks)
    return PROMPT_TEMPLATE.replace("{READER_PROFILE}", reader_profile).replace(
        "{ARTICLES_BY_CATEGORY}", articles_block
    )


def _parse_synthesis(response_text: str) -> Optional[BriefSynthesis]:
    """Parse and validate a synthesis response into a ``BriefSynthesis``."""
    data = extract_json_from_text(response_text)
    if not data:
        return None
    payload = dict(data)
    cross_cutting = payload.pop("cross_cutting", []) or []
    personal = payload.pop("personal", None)
    try:
        return BriefSynthesis(
            categories=payload,
            cross_cutting=cross_cutting,
            personal=personal,
        )
    except pydantic.ValidationError as exc:
        logger.warning("Brief synthesis failed schema validation: %s", exc)
        return None


def synthesize(
    synthesis_input: Dict[str, List[Dict[str, str]]],
    reader_profile: str,
    model: str,
    client: Optional[Any] = None,
) -> Optional[BriefSynthesis]:
    """Run one Claude synthesis call (with one retry) and validate the result.

    Returns ``None`` on persistent failure so the caller can skip the brief
    without blocking the main digest. Never raises.
    """
    if not synthesis_input:
        logger.info("No themed articles to synthesise; skipping brief")
        return None

    if client is None:
        client = anthropic.Anthropic(api_key=get_anthropic_api_key())

    prompt = build_prompt(synthesis_input, reader_profile)
    api_timeout = int(os.environ.get("CLAUDE_API_TIMEOUT", "120"))

    for attempt in (1, 2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=SYNTHESIS_MAX_TOKENS,
                temperature=0.3,
                messages=[{"role": "user", "content": prompt}],
                timeout=api_timeout,
            )
            response_text = response.content[0].text.strip()
            brief = _parse_synthesis(response_text)
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
            return brief
        logger.warning(
            "Brief synthesis JSON parse/validation failed (attempt %d)", attempt
        )

    logger.error("Brief synthesis failed after retry; skipping brief for this run")
    return None


def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace for title matching."""
    return re.sub(r"\s+", " ", text).strip().lower()


@pydantic.validate_call(validate_return=True)
def build_article_map(
    synthesis_input: Dict[str, List[Dict[str, str]]]
) -> Dict[str, str]:
    """Build a ``title -> url`` map from the synthesis input."""
    article_map: Dict[str, str] = {}
    for items in synthesis_input.values():
        for item in items:
            if item.get("url"):
                article_map[item["title"]] = item["url"]
    return article_map


@pydantic.validate_call(validate_return=True)
def match_title_to_url(title: str, article_map: Dict[str, str]) -> Optional[str]:
    """Resolve an article title to a URL via the fallback chain.

    Exact title -> normalised/lowercased -> word-overlap (Jaccard) >= 0.75 ->
    ``None`` (caller renders plain text).
    """
    if not title:
        return None
    if title in article_map:
        return article_map[title]

    target = _normalise(title)
    normalised = {_normalise(key): url for key, url in article_map.items()}
    if target in normalised:
        return normalised[target]

    target_words = set(target.split())
    if target_words:
        best_url: Optional[str] = None
        best_score = 0.0
        for original, url in article_map.items():
            words = set(_normalise(original).split())
            if not words:
                continue
            overlap = len(target_words & words) / len(target_words | words)
            if overlap > best_score:
                best_score = overlap
                best_url = url
        if best_score >= WORD_OVERLAP_THRESHOLD:
            return best_url
    return None


def _signal_badge(signal: str) -> str:
    """Render a signal-strength badge as an inline-styled span."""
    background, border, text = SIGNAL_BADGE_STYLES.get(
        signal, SIGNAL_BADGE_STYLES["GENERAL"]
    )
    return (
        f'<span style="display: inline-block; padding: 2px 8px; font-size: 12px; '
        f'font-weight: bold; border-radius: 4px; background-color: {background}; '
        f'border: 1px solid {border}; color: {text};">{html.escape(signal)}</span>'
    )


def _render_article_links(titles: List[str], article_map: Dict[str, str]) -> str:
    """Render a list of article titles as links, plain text if unmatched."""
    parts = []
    for title in titles:
        url = match_title_to_url(title, article_map)
        safe_title = html.escape(title)
        if url:
            parts.append(
                f'<li style="margin: 0 0 6px 0;">'
                f'<a href="{html.escape(url)}" target="_blank" '
                f'style="color: #0066cc; text-decoration: underline;">'
                f"{safe_title}</a></li>"
            )
        else:
            parts.append(
                f'<li style="margin: 0 0 6px 0; color: #555;">{safe_title}</li>'
            )
    if not parts:
        return ""
    return (
        '<ul style="margin: 8px 0 0 0; padding-left: 20px; font-size: 14px;">'
        + "".join(parts)
        + "</ul>"
    )


def _render_theme(theme: BriefTheme, article_map: Dict[str, str]) -> str:
    """Render a single theme: badge, name, tldr, relevance, linked articles."""
    parts = [
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 18px 0;"><tr><td '
        'style="padding: 14px 16px; background-color: #f8f9fa; '
        'border-left: 4px solid #3498db;">',
        f'<p style="margin: 0 0 8px 0; font-size: 16px; color: #2c3e50;">'
        f"{_signal_badge(theme.signal_strength)} "
        f"<strong>{html.escape(theme.theme)}</strong></p>",
        f'<p style="margin: 0 0 10px 0; font-size: 16px; color: #555; '
        f'line-height: 1.6;">{html.escape(theme.tldr)}</p>',
    ]
    if theme.relevance_to_reader:
        parts.append(
            f'<p style="margin: 0 0 8px 0; font-size: 14px; color: #1a5276; '
            f'line-height: 1.5;"><strong>Why this matters to you:</strong> '
            f"{html.escape(theme.relevance_to_reader)}</p>"
        )
    parts.append(_render_article_links(theme.top_articles, article_map))
    parts.append("</td></tr></table>")
    return "".join(parts)


def _render_category(
    name: str, category: BriefCategory, article_map: Dict[str, str]
) -> str:
    """Render a themed category: coloured header, verdict, then themes."""
    header = (
        f'<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        f'style="background-color: {category_color(name)}; border-radius: 6px; '
        f'margin: 0 0 12px 0;"><tr><td>'
        f'<h2 style="color: #ffffff; margin: 0; font-size: 20px; '
        f'font-weight: bold; line-height: 1.3;">{html.escape(name)}</h2>'
        f"</td></tr></table>"
    )
    verdict = ""
    if category.week_verdict:
        verdict = (
            f'<p style="margin: 0 0 14px 0; font-size: 15px; color: #2c3e50; '
            f'font-style: italic;">{html.escape(category.week_verdict)}</p>'
        )
    themes = "".join(_render_theme(theme, article_map) for theme in category.themes)
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + verdict + themes + "</td></tr></table>"
    )


def _render_cross_cutting(signals: List[CrossCuttingSignal]) -> str:
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
            f'<p style="margin: 0 0 6px 0; font-size: 16px; color: #2c3e50;">'
            f"<strong>{html.escape(signal.signal)}</strong></p>"
            f'<p style="margin: 0 0 6px 0; font-size: 13px; color: #666;">{cats}</p>'
            f'<p style="margin: 0; font-size: 16px; color: #555; '
            f'line-height: 1.6;">{html.escape(signal.implication)}</p>'
            "</td></tr></table>"
        )
    header = (
        '<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        'style="background-color: #667eea; border-radius: 6px; '
        'margin: 0 0 12px 0;"><tr><td>'
        '<h2 style="color: #ffffff; margin: 0; font-size: 20px; '
        'font-weight: bold;">Cross-Cutting Signals</h2></td></tr></table>'
    )
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + "".join(rows) + "</td></tr></table>"
    )


def _render_personal(
    personal: Optional[PersonalBlock], article_map: Dict[str, str]
) -> str:
    """Render the personal-interest digest block (e.g. Cycling)."""
    if personal is None:
        return ""
    header = (
        '<table width="100%" cellpadding="12" cellspacing="0" border="0" '
        'style="background-color: #16a085; border-radius: 6px; '
        'margin: 0 0 12px 0;"><tr><td>'
        '<h2 style="color: #ffffff; margin: 0; font-size: 20px; '
        'font-weight: bold;">Personal</h2></td></tr></table>'
    )
    summary = ""
    if personal.summary:
        summary = (
            f'<p style="margin: 0 0 10px 0; font-size: 16px; color: #555; '
            f'line-height: 1.6;">{html.escape(personal.summary)}</p>'
        )
    links = _render_article_links(personal.top_stories, article_map)
    return (
        '<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin: 0 0 30px 0;"><tr><td>' + header + summary + links + "</td></tr></table>"
    )


@pydantic.validate_call(validate_return=True)
def render_brief_html(
    brief: BriefSynthesis,
    article_map: Dict[str, str],
    date: str,
    article_count: int,
    themed_order: Optional[List[str]] = None,
) -> str:
    """Render the validated synthesis into an email-safe HTML body."""
    ordered: List[str] = []
    for name in list(themed_order or []) + list(brief.categories.keys()):
        if name in brief.categories and name not in ordered:
            ordered.append(name)

    sections = [
        _render_category(name, brief.categories[name], article_map) for name in ordered
    ]
    sections.append(_render_cross_cutting(brief.cross_cutting))
    sections.append(_render_personal(brief.personal, article_map))
    brief_content = "\n".join(section for section in sections if section)

    template = files("rss_email").joinpath("brief_body.html").read_text(encoding="utf-8")
    return template.format(
        subject=f"RSS Brief — {date}",
        generation_time=date,
        article_count=article_count,
        brief_content=brief_content,
    )


def generate_brief(
    categories: Dict[str, List[Any]],
    *,
    date: str,
    article_count: int,
    client: Optional[Any] = None,
) -> Optional[str]:
    """Build and render the RSS Brief email body.

    Returns the HTML body, or ``None`` if the brief is disabled, there is no
    themed/personal content, or synthesis failed. Never raises.
    """
    config = load_brief_config()
    if not config.get("enabled", True):
        logger.info("RSS Brief is disabled; skipping")
        return None

    synthesis_input = build_synthesis_input(
        categories,
        config.get("themed_categories", []),
        config.get("personal_categories", []),
    )
    if not synthesis_input:
        logger.info("No themed/personal articles available; skipping brief")
        return None

    brief = synthesize(
        synthesis_input,
        config.get("reader_profile", ""),
        config.get("model", DEFAULT_SYNTHESIS_MODEL),
        client=client,
    )
    if brief is None:
        return None

    article_map = build_article_map(synthesis_input)
    return render_brief_html(
        brief,
        article_map,
        date,
        article_count,
        themed_order=config.get("themed_categories", []),
    )
