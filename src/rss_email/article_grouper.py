"""Cluster RSS items into logical articles via Claude (step 1 of 2-stage flow).

Grouping rule: ONLY merge items that report the same specific news event
(release, announcement, study, breach, hire, ...). Different angles on the
same topic stay in separate groups. When in doubt, keep articles separate.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import anthropic
import pydantic
from pydantic.config import ConfigDict

from .article_processor import (
    ClaudeRateLimiter,
    estimate_tokens,
    get_anthropic_api_key,
    optimize_articles_for_claude,
)
from .json_utils import extract_json_from_text

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def create_grouping_prompt(
    articles: List[Dict[str, Any]], batch_offset: int = 0
) -> str:
    """Build the prompt for Claude to cluster articles by same-event similarity."""
    optimized = optimize_articles_for_claude(articles)
    articles_json = []
    for idx, article in enumerate(optimized):
        articles_json.append(
            {
                "id": f"article_{batch_offset + idx}",
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "pubdate": article.get("pubDate", ""),
            }
        )

    return f"""You are clustering {len(articles_json)} RSS articles into groups.

GROUPING RULE (STRICT):
- Group two or more articles together ONLY when they cover the SAME SPECIFIC
  NEWS EVENT - the same product launch, the same study, the same breach,
  the same earnings report, the same hire, the same incident.
- DO NOT group articles that merely share a topic, industry, or theme.
- DO NOT group analysis pieces that take different angles on the same subject
  unless they are clearly reporting the same announcement.
- When in doubt, KEEP ARTICLES SEPARATE. Singleton groups are the default.

OUTPUT RULES:
- Return ONLY valid JSON, no commentary.
- Every input article id must appear in EXACTLY ONE group.
- Most groups will contain a single article. That is expected.

Required JSON shape:
{{
  "groups": [
    ["article_3", "article_7"],
    ["article_0"],
    ["article_1"]
  ],
  "article_count": {len(articles_json)}
}}

Articles to cluster:
{json.dumps(articles_json, indent=2)}
"""


def parse_grouping_response(
    text: str, article_count: int, batch_offset: int = 0
) -> List[List[int]]:
    """Parse Claude's grouping JSON into lists of article indices.

    - Indices are returned relative to the original article list (i.e. the
      batch_offset is subtracted from the article_N id).
    - Any index not present in the response is added as a singleton group so
      no article is dropped.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Claude sometimes wraps JSON in markdown fences; extract_json_from_text
        # strips them and applies multiple repair strategies.
        data = extract_json_from_text(text, required_fields=["groups"])
        if data is None:
            logger.error("Failed to parse grouping response as JSON")
            return [[i] for i in range(article_count)]

    raw_groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(raw_groups, list):
        logger.warning("Grouping response missing 'groups' list; using singletons")
        return [[i] for i in range(article_count)]

    seen: set[int] = set()
    groups: List[List[int]] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, list):
            continue
        indices: List[int] = []
        for raw_id in raw_group:
            if not isinstance(raw_id, str) or not raw_id.startswith("article_"):
                continue
            try:
                idx = int(raw_id.split("_", 1)[1]) - batch_offset
            except (ValueError, IndexError):
                continue
            if 0 <= idx < article_count and idx not in seen:
                indices.append(idx)
                seen.add(idx)
        if indices:
            groups.append(indices)

    # Add any missing article as a singleton so nothing is silently dropped
    for i in range(article_count):
        if i not in seen:
            groups.append([i])

    return groups


@pydantic.validate_call(
    validate_return=True, config=ConfigDict(arbitrary_types_allowed=True)
)
def group_articles_with_claude(
    articles: List[Dict[str, Any]], rate_limiter: ClaudeRateLimiter
) -> Optional[List[List[int]]]:
    """Cluster articles via Claude. Returns groups of indices into ``articles``.

    Returns None on hard failures so callers can fall back to singleton groups.
    """
    if not articles:
        return []

    if os.environ.get("CLAUDE_ENABLED", "true").lower() != "true":
        logger.info("Claude disabled - skipping grouping step")
        return [[i] for i in range(len(articles))]

    estimated_tokens = estimate_tokens(articles)
    if not rate_limiter.can_make_request(estimated_tokens):
        logger.warning("Rate limit would be exceeded for grouping call")
        return None

    try:
        client = anthropic.Anthropic(api_key=get_anthropic_api_key())
        prompt = create_grouping_prompt(articles)

        model_name = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
        api_timeout = int(os.environ.get("CLAUDE_API_TIMEOUT", "120"))

        response = client.messages.create(
            model=model_name,
            max_tokens=4096,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
            timeout=api_timeout,
        )

        response_text = response.content[0].text.strip()
        tokens_used = response.usage.input_tokens + response.usage.output_tokens
        rate_limiter.record_usage(tokens_used)

        return parse_grouping_response(response_text, len(articles))

    except (anthropic.APIError, ValueError, KeyError, IndexError) as exc:
        logger.error("Grouping call failed: %s", exc)
        return None
