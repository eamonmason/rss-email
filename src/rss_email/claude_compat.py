"""Helpers that keep the Haiku call sites working across Haiku 4.5 and Haiku 5.5.

Haiku 5.5 thinks by default (so a response can open with a thinking block, and
thinking tokens are billed as output) and has no server-side refusal fallback.
Haiku 4.5 rejects ``output_config.effort``. These helpers hide both differences.
"""

import os
from typing import Any, Dict

# Grouping and categorising are extraction work: the lowest effort is enough, and
# every thinking token is paid for as output. Override with CLAUDE_HAIKU_EFFORT
# (for example "medium") if the assessment in eval/compare_haiku.py shows it helps.
DEFAULT_HAIKU_EFFORT = "low"


def response_text(message: Any) -> str:
    """Join the ``text`` blocks of a Messages response, ignoring thinking blocks.

    Returns an empty string when there is no text (for example a refusal).
    """
    blocks = getattr(message, "content", None) or []
    # Thinking and tool blocks carry no ``text`` string; text blocks always do.
    return "".join(
        block.text for block in blocks if isinstance(getattr(block, "text", None), str)
    ).strip()


def effort_params(model: str) -> Dict[str, Any]:
    """Request parameters that set low effort, for Haiku 5 models only."""
    if not model.startswith("claude-haiku-5"):
        return {}
    effort = os.environ.get("CLAUDE_HAIKU_EFFORT", DEFAULT_HAIKU_EFFORT)
    return {"output_config": {"effort": effort}}
