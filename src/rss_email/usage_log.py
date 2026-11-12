"""Structured token-usage logging for Claude calls.

One ``CLAUDE_USAGE`` log line per call site (or per batch), so spend by step can
be read from CloudWatch without guessing. Tokens only: prices change, so cost is
worked out from these counts at query time. Never raises.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _count(usage: Any, field: str) -> int:
    value = getattr(usage, field, 0)
    return value if isinstance(value, int) else 0


def log_usage(
    step: str,
    model: str,
    usage: Any,
    *,
    stop_reason: Optional[str] = None,
    batch: bool = False,
    requests: int = 1,
) -> None:
    """Log token usage for one Claude call (or an aggregated batch).

    Args:
        step: Pipeline step, e.g. ``grouping``, ``categorise_batch``, ``brief``.
        model: Model id used.
        usage: Anthropic usage object (missing fields count as 0).
        stop_reason: Optional stop reason, useful for spotting ``max_tokens``.
        batch: True when billed at Batch API rates.
        requests: Number of API requests the figures cover.
    """
    try:
        counts = {field: _count(usage, field) for field in _FIELDS}
        logger.info(
            "CLAUDE_USAGE step=%s model=%s batch=%s requests=%d "
            "input=%d output=%d cache_write=%d cache_read=%d stop=%s",
            step,
            model,
            batch,
            requests,
            counts["input_tokens"],
            counts["output_tokens"],
            counts["cache_creation_input_tokens"],
            counts["cache_read_input_tokens"],
            stop_reason,
        )
    except Exception:  # pylint: disable=broad-except
        logger.debug("Could not log Claude usage", exc_info=True)
