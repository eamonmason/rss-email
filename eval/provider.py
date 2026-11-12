"""promptfoo custom provider that runs the real RSS Brief synthesis pipeline.

promptfoo calls ``call_api`` once per test case. We load the fixture digest
(``vars.fixture`` — a categories -> [{title,url,summary,source}] JSON file, the
same shape ``build_synthesis_input`` produces), run the shipping
``brief_generator.synthesize`` against it using the live config, and return the
validated ``BriefSynthesis`` as JSON for the assertions to grade.

Requires ``ANTHROPIC_API_KEY`` in the environment (see ``get_anthropic_api_key``).

The provider's ``config`` block in ``promptfooconfig.yaml`` may override the live
``brief_config.json`` keys ``model``, ``effort`` and ``max_tokens``, so one eval run
can compare model/effort variants on identical fixtures. Token usage is captured
from the ``CLAUDE_USAGE`` log lines the pipeline already emits and returned to
promptfoo together with a cost computed from ``prices.json``.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

# Make ``rss_email`` importable when promptfoo runs this from the repo root.
_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# pylint: disable=wrong-import-position
from rss_email.brief_generator import (  # noqa: E402
    ensure_article_ids,
    load_brief_config,
    synthesize,
)


_OVERRIDABLE = ("model", "effort", "max_tokens")
_PRICES_PATH = os.path.join(os.path.dirname(__file__), "prices.json")


class _UsageCapture(logging.Handler):
    """Sums input/output tokens from ``log_usage`` records for one step."""

    def __init__(self, step: str):
        super().__init__(level=logging.INFO)
        self.step = step
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    def emit(self, record: logging.LogRecord) -> None:
        # log_usage args: step, model, batch, requests, input, output, ...
        args = record.args
        if not isinstance(args, tuple) or len(args) < 6 or args[0] != self.step:
            return
        self.input_tokens += int(args[4])
        self.output_tokens += int(args[5])
        self.calls += 1


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """Return the USD cost of the given usage, or None if the model has no price."""
    with open(_PRICES_PATH, "r", encoding="utf-8") as handle:
        table = json.load(handle)
    price = table.get(model) or table.get("baseline_prices", {}).get(model)
    if not isinstance(price, dict):
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def apply_overrides(config: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Return ``config`` with the allowed override keys applied (others ignored)."""
    merged = dict(config)
    merged.update({k: v for k, v in (overrides or {}).items() if k in _OVERRIDABLE})
    return merged


def fixture_date(fixture_path: str) -> str:
    """Date of a fixture, from its directory name (``2026-06-15`` or ``2026-06-15-lowrank``)."""
    return os.path.basename(os.path.dirname(os.path.abspath(fixture_path)))[:10]


def call_api(prompt: str, options: Dict[str, Any], context: Dict[str, Any]):
    """Synthesise a brief from the fixture digest and return it as JSON.

    Args:
        prompt: Unused - the prompt is built internally by ``build_prompt``.
        options: promptfoo provider options; ``options["config"]`` may carry
            ``model`` / ``effort`` / ``max_tokens`` overrides.
        context: Must contain ``vars.fixture`` (path to the digest fixture).

    Returns:
        ``{"output": <BriefSynthesis JSON>, "tokenUsage": ..., "cost": ...}`` on
        success, or ``{"error": ...}``.
    """
    del prompt  # the real prompt is assembled inside synthesize()
    fixture_path = context.get("vars", {}).get("fixture")
    if not fixture_path:
        return {"error": "no 'fixture' var provided to the provider"}

    with open(fixture_path, "r", encoding="utf-8") as handle:
        synthesis_input = json.load(handle)

    # Fixtures predate the "id" field build_synthesis_input now assigns in the
    # real pipeline; backfill it here so build_prompt (which cites articles by
    # id) works against fixtures loaded straight from disk.
    ensure_article_ids(synthesis_input)

    config = apply_overrides(load_brief_config(), (options or {}).get("config", {}))

    capture = _UsageCapture("brief")
    usage_logger = logging.getLogger("rss_email.usage_log")
    previous_level = usage_logger.level
    usage_logger.setLevel(logging.INFO)
    usage_logger.addHandler(capture)
    try:
        brief = synthesize(
            synthesis_input, config, date=fixture_date(fixture_path)
        )
    finally:
        usage_logger.removeHandler(capture)
        usage_logger.setLevel(previous_level)

    usage = {
        "prompt": capture.input_tokens,
        "completion": capture.output_tokens,
        "total": capture.input_tokens + capture.output_tokens,
    }
    if brief is None:
        return {
            "error": "synthesis returned None (parse/validation/API failure)",
            "tokenUsage": usage,
            "cost": cost_usd(config["model"], usage["prompt"], usage["completion"]),
        }

    return {
        "output": brief.model_dump_json(),
        "tokenUsage": usage,
        "cost": cost_usd(config["model"], usage["prompt"], usage["completion"]),
        "metadata": {"model": config["model"], "effort": config.get("effort"),
                     "api_calls": capture.calls},
    }
