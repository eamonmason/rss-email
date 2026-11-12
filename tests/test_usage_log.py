"""Tests for token-usage logging (usage_log.py)."""
# pylint: disable=missing-function-docstring

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

from rss_email.usage_log import log_usage


def test_logs_counts_and_stop_reason(caplog):
    usage = SimpleNamespace(input_tokens=1200, output_tokens=3400)
    with caplog.at_level(logging.INFO, logger="rss_email.usage_log"):
        log_usage("brief", "claude-opus-5-5", usage, stop_reason="end_turn")
    line = caplog.messages[0]
    assert line.startswith("CLAUDE_USAGE step=brief model=claude-opus-5-5 batch=False")
    assert "input=1200 output=3400 cache_write=0 cache_read=0 stop=end_turn" in line


def test_batch_flag_and_request_count(caplog):
    with caplog.at_level(logging.INFO, logger="rss_email.usage_log"):
        log_usage("categorise_batch", "m", SimpleNamespace(input_tokens=1, output_tokens=2),
                  batch=True, requests=7)
    assert "batch=True requests=7" in caplog.messages[0]


def test_tolerates_mock_usage_and_never_raises(caplog):
    with caplog.at_level(logging.INFO, logger="rss_email.usage_log"):
        log_usage("grouping", "m", MagicMock())
    assert "input=0 output=0" in caplog.messages[0]
    log_usage("grouping", "m", None)
