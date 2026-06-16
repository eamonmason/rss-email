"""promptfoo custom provider that runs the real RSS Brief synthesis pipeline.

promptfoo calls ``call_api`` once per test case. We load the fixture digest
(``vars.fixture`` — a categories -> [{title,url,summary,source}] JSON file, the
same shape ``build_synthesis_input`` produces), run the shipping
``brief_generator.synthesize`` against it using the live config, and return the
validated ``BriefSynthesis`` as JSON for the assertions to grade.

Requires ``ANTHROPIC_API_KEY`` in the environment (see ``get_anthropic_api_key``).
"""

import json
import os
import sys
from typing import Any, Dict

# Make ``rss_email`` importable when promptfoo runs this from the repo root.
_SRC = os.path.join(os.path.dirname(__file__), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# pylint: disable=wrong-import-position
from rss_email.brief_generator import load_brief_config, synthesize  # noqa: E402


def call_api(prompt: str, options: Dict[str, Any], context: Dict[str, Any]):
    """Synthesise a brief from the fixture digest and return it as JSON.

    Args:
        prompt: Unused — the prompt is built internally by ``build_prompt``.
        options: promptfoo provider options (unused).
        context: Must contain ``vars.fixture`` (path to the digest fixture).

    Returns:
        ``{"output": <BriefSynthesis JSON>}`` on success, or ``{"error": ...}``.
    """
    del prompt, options  # the real prompt is assembled inside synthesize()
    fixture_path = context.get("vars", {}).get("fixture")
    if not fixture_path:
        return {"error": "no 'fixture' var provided to the provider"}

    with open(fixture_path, "r", encoding="utf-8") as handle:
        synthesis_input = json.load(handle)

    config = load_brief_config()
    brief = synthesize(synthesis_input, config)
    if brief is None:
        return {"error": "synthesis returned None (parse/validation/API failure)"}

    return {"output": brief.model_dump_json()}
