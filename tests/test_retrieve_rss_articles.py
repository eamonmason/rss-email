"""Tests for retrieve_rss_articles module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from rss_email.retrieve_rss_articles import lambda_handler


@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables for testing."""
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("KEY", "rss.xml")
    monkeypatch.setenv("FEED_URLS_BUCKET", "test-bucket")
    monkeypatch.setenv("FEED_URLS_KEY", "feed_urls.json")


@patch("rss_email.retrieve_rss_articles.retrieve_rss_feeds")
@patch("rss_email.retrieve_rss_articles.boto3.client")
def test_lambda_handler_tolerates_disabled_feed_url_convention(
    mock_boto3_client, mock_retrieve_rss_feeds, mock_env
):
    """A feed disabled via the documented "_url" key must not crash feed_stats.

    FeedConfig.from_dict() explicitly supports {"_url": ..., "name": ...} for
    disabled feeds, but the handler previously built url_to_name with a plain
    dict comprehension keyed on `feed["url"]`, which raises KeyError the first
    time a feed uses that convention.
    """
    feed_urls_content = json.dumps(
        {
            "feeds": [
                {"name": "Active Feed", "url": "https://example.com/feed"},
                {"name": "Disabled Feed", "_url": "https://example.com/off"},
            ]
        }
    )

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: feed_urls_content.encode("utf-8"))
    }
    mock_boto3_client.return_value = mock_s3

    mock_retrieve_rss_feeds.return_value = (
        "<rss><channel></channel></rss>",
        {"https://example.com/feed": 3},
    )

    result = lambda_handler({}, None)

    assert result["s3_bucket"] == "test-bucket"

    # Find the feed_stats.json upload and confirm the URL was resolved to its name.
    feed_stats_calls = [
        call for call in mock_s3.put_object.call_args_list
        if call.kwargs.get("Key") == "feed_stats.json"
    ]
    assert len(feed_stats_calls) == 1
    stored_stats = json.loads(feed_stats_calls[0].kwargs["Body"])
    assert stored_stats == {"Active Feed": 3}
