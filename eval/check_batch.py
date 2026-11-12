"""Check that the Batch API accepts the Haiku 5.5 request settings production would send.

    ANTHROPIC_API_KEY=... uv run python eval/check_batch.py [model]

Creates a one-request batch with the same parameters ``submit_email_batch`` builds for the
model (including ``output_config.effort`` for Haiku 5 models), reports whether it was
accepted, and cancels it straight away so nothing is processed or billed in practice.
Submitting a batch that the API rejects would otherwise only be found out at 07:30 UTC.
"""

import os
import sys
from typing import Any, List

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# pylint: disable=wrong-import-position
import anthropic  # noqa: E402

from rss_email.submit_email_batch import create_batch_requests  # noqa: E402

DEFAULT_MODEL = "claude-haiku-5-5"
SAMPLE_ARTICLES = [{
    "title": "Example story", "description": "A short example description.",
    "link": "https://example.com/1", "pubDate": "Mon, 01 Jan 2026 08:00:00 GMT",
}]


def check(client: Any, model: str) -> str:
    """Create and cancel a one-request batch. Returns ``"accepted"`` or the rejection text."""
    requests = create_batch_requests([[("group_0", [0])]], SAMPLE_ARTICLES, model)
    try:
        batch = client.messages.batches.create(requests=requests)
    except anthropic.BadRequestError as exc:
        return f"rejected: {exc}"
    try:
        client.messages.batches.cancel(batch.id)
    except anthropic.APIError as exc:  # the batch exists; say so rather than hide it
        return f"accepted (batch {batch.id}; cancel failed: {exc})"
    return "accepted"


def main(argv: List[str]) -> int:
    """CLI entry point; exit status 0 only if the batch was accepted."""
    model = argv[0] if argv else DEFAULT_MODEL
    result = check(anthropic.Anthropic(), model)
    print(f"{model}: {result}")
    return 0 if result.startswith("accepted") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
