"""Narration script for the podcast, written from the day's RSS Brief.

The brief has already done the editorial work (what matters, in what order), so
the podcast is a single narrator reading a connected piece of prose, not a
second summarisation of the raw feed. ``build_narration_input`` flattens a
``BriefSynthesis`` into plain records (no article ids, no URLs) that are stored
in S3 by the email Lambda and read back by the podcast Lambda.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .brief_prompt import FIDELITY_RULE
from .models import BriefSynthesis

try:
    import anthropic
except ImportError:  # pragma: no cover - the layer always ships anthropic
    anthropic = None

logger = logging.getLogger(__name__)

NARRATION_INPUT_KEY = "podcast/narration-input.json"
DEFAULT_PODCAST_MODEL = "claude-sonnet-5-5"

NARRATION_PROMPT = """\
You are the scriptwriter for a daily audio briefing in the manner of The Economist's
audio edition: one authoritative, unhurried narrator reading a single connected piece.
Write the script for TODAY ({date}) from the editorial material below.

VOICE:
- One narrator, speaking directly to a busy, informed listener. No co-host, no
  dialogue, no speaker labels.
- Dry, precise and a little wry. British spelling. Plain words over jargon; explain
  a technical term in half a sentence when it matters.
- No filler, no hype, no greetings such as "folks" or "buckle up", no exclamation marks.

SHAPE:
- Open with the date and the single most important story, in one or two sentences.
- Treat "Read these" and the themes as raw material for a narrative: group related
  stories, say why each matters and what it changes, and move between them with
  proper transitions rather than reading a list.
- Where several stories point the same way, say so. Use the cross-cutting signals.
- Give the personal-interest items (if any) a light touch near the end.
- Close with a short "one to watch" and a plain sign-off line. Do not announce that
  the briefing is ending.
- Length: {min_words} to {max_words} words.

{FIDELITY_RULE}

TEXT-TO-SPEECH RULES (the script is read aloud by a speech model):
- Plain prose in short paragraphs separated by blank lines. No markdown, headings,
  bullets, numbered lists, URLs, brackets or stage directions.
- Write numbers, symbols and abbreviations as they should be spoken: "forty per cent",
  "two billion dollars". Write AI as "AI" (never "A.I."); spell out unusual acronyms the
  first time.
- Keep sentences under about thirty words so the narrator can breathe.
- Name the publication when it adds credibility ("according to Reuters"), never
  say "link" or "article" in the sense of a web page.

EDITORIAL MATERIAL:
{material}
"""

MIN_WORDS = 1100
MAX_WORDS = 1400


def _record(article_index: Dict[str, Dict[str, str]], article_id: str) -> Optional[Dict[str, str]]:
    entry = article_index.get(article_id)
    if not entry:
        return None
    return {"title": entry.get("title", ""), "source": entry.get("source", "")}


def build_narration_input(
    synthesis: BriefSynthesis,
    article_index: Dict[str, Dict[str, str]],
    date: str,
) -> Dict[str, Any]:
    """Flatten a brief into the id-free, URL-free record the narrator works from."""
    must_read: List[Dict[str, str]] = []
    for item in synthesis.must_read:
        record = _record(article_index, item.id)
        if record:
            must_read.append({**record, "why": item.why})

    categories: List[Dict[str, Any]] = []
    for name, category in synthesis.categories.items():
        themes = []
        for theme in category.themes:
            sources = [r for r in (_record(article_index, i) for i in theme.top_articles) if r]
            themes.append({
                "theme": theme.theme,
                "signal_strength": theme.signal_strength,
                "tldr": theme.tldr,
                "relevance": theme.relevance_to_reader or "",
                "articles": sources,
            })
        if themes:
            categories.append({"category": name, "themes": themes})

    cross_cutting = [
        {"signal": s.signal, "implication": s.implication}
        for s in synthesis.cross_cutting
    ]

    personal = None
    if synthesis.personal and (synthesis.personal.summary or synthesis.personal.top_stories):
        stories = [
            r for r in (_record(article_index, i) for i in synthesis.personal.top_stories) if r
        ]
        personal = {"summary": synthesis.personal.summary, "stories": stories}

    return {
        "date": date,
        "must_read": must_read,
        "categories": categories,
        "cross_cutting": cross_cutting,
        "personal": personal,
    }


def render_material(narration_input: Dict[str, Any]) -> str:
    """Render the narration input as the compact text block placed in the prompt."""
    lines: List[str] = ["READ THESE (the lead stories, most important first):"]
    for item in narration_input.get("must_read", []):
        lines.append(f"- [{item['source']}] {item['title']}: {item['why']}")

    for category in narration_input.get("categories", []):
        lines.append(f"\n{category['category'].upper()}:")
        for theme in category["themes"]:
            lines.append(f"- {theme['theme']} ({theme['signal_strength']}): {theme['tldr']}")
            if theme["relevance"]:
                lines.append(f"  Why it matters to the listener: {theme['relevance']}")
            for article in theme["articles"]:
                lines.append(f"  Source: [{article['source']}] {article['title']}")

    if narration_input.get("cross_cutting"):
        lines.append("\nCROSS-CUTTING SIGNALS:")
        for signal in narration_input["cross_cutting"]:
            lines.append(f"- {signal['signal']} Implication: {signal['implication']}")

    personal = narration_input.get("personal")
    if personal:
        lines.append(f"\nPERSONAL INTERESTS: {personal['summary']}")
        for story in personal["stories"]:
            lines.append(f"- [{story['source']}] {story['title']}")

    return "\n".join(lines)


def build_prompt(narration_input: Dict[str, Any]) -> str:
    """Compose the full narration prompt (``str.replace`` keeps stray braces literal)."""
    prompt = NARRATION_PROMPT
    for placeholder, value in (
        ("{date}", str(narration_input.get("date", ""))),
        ("{min_words}", str(MIN_WORDS)),
        ("{max_words}", str(MAX_WORDS)),
        ("{FIDELITY_RULE}", FIDELITY_RULE),
        ("{material}", render_material(narration_input)),
    ):
        prompt = prompt.replace(placeholder, value)
    return prompt


def save_narration_input(bucket: str, narration_input: Dict[str, Any],
                         key: str = NARRATION_INPUT_KEY) -> bool:
    """Store the narration input for the podcast Lambda. Never raises."""
    try:
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(narration_input).encode("utf-8"),
            ContentType="application/json",
        )
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.error("Failed to save narration input: %s", exc)
        return False


def load_narration_input(bucket: str, key: str = NARRATION_INPUT_KEY) -> Optional[Dict[str, Any]]:
    """Read the narration input, or ``None`` when absent or unreadable."""
    try:
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body.decode("utf-8"))
    except ClientError as exc:
        logger.info("No narration input available: %s", exc)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("Narration input is unreadable: %s", exc)
    return None


def generate_narration(narration_input: Dict[str, Any], client: Any) -> Optional[str]:
    """Write the narration script with one direct Claude call. Never raises."""
    if anthropic is None:
        logger.error("Anthropic library not installed.")
        return None
    try:
        message = client.messages.create(
            model=os.environ.get("PODCAST_CLAUDE_MODEL", DEFAULT_PODCAST_MODEL),
            max_tokens=int(os.environ.get("PODCAST_MAX_TOKENS", "8000")),
            messages=[{"role": "user", "content": build_prompt(narration_input)}],
        )
    except (anthropic.APIError, RuntimeError, ValueError) as exc:
        logger.error("Error generating narration with Claude: %s", exc)
        return None

    # Read by block type: newer models can return non-text (thinking) blocks first.
    script = "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    ).strip()
    if not script:
        logger.error("Claude returned no narration text")
        return None
    logger.info("Narration generated (%d words)", len(script.split()))
    return script
