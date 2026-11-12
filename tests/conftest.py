"""Shared test configuration."""
# pylint: disable=missing-function-docstring

import pytest


@pytest.fixture(autouse=True)
def _podcast_enabled_by_default(monkeypatch):
    """Keep the podcast switch off the network; tests override as needed."""
    monkeypatch.setenv("PODCAST_ENABLED", "true")
