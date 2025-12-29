"""Tests for submit_email_batch module."""
# pylint: disable=redefined-outer-name,unused-argument,too-many-positional-arguments

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from rss_email.submit_email_batch import lambda_handler


@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables for testing."""
    monkeypatch.setenv("RSS_BUCKET", "test-bucket")
    monkeypatch.setenv("RSS_KEY", "rss.xml")
    monkeypatch.setenv("LAST_RUN_PARAMETER", "test-lastrun")
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("CLAUDE_BATCH_SIZE", "25")


@pytest.fixture
def sample_articles():
    """Sample articles for testing."""
    return [
        {
            "title": "Article 1",
            "link": "https://example.com/1",
            "description": "Description 1",
            "pubDate": "Mon, 29 Dec 2025 12:00:00 GMT",
        },
        {
            "title": "Article 2",
            "link": "https://example.com/2",
            "description": "Description 2",
            "pubDate": "Mon, 29 Dec 2025 11:00:00 GMT",
        },
    ]


@patch("rss_email.submit_email_batch.anthropic.Anthropic")
@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
def test_lambda_handler_success(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    mock_env,
    sample_articles,
):
    """Test successful batch submission with articles."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-123"
    mock_anthropic.return_value.messages.batches.create.return_value = mock_batch

    # Execute
    result = lambda_handler({}, None)

    # Verify
    assert result["batch_id"] == "batch-123"
    assert result["request_count"] == 1  # 2 articles fit in 1 batch
    assert result["articles_count"] == 2
    assert "submitted_at" in result

    # Verify API calls
    mock_anthropic.return_value.messages.batches.create.assert_called_once()
    mock_ssm.get_parameter.assert_called_once_with(
        Name="test-api-key-param", WithDecryption=True
    )


@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
@patch("rss_email.submit_email_batch.boto3.client")
def test_lambda_handler_no_articles(
    mock_boto3_client,
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_env,
):
    """Test handler when no articles to process."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 29, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = []

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Execute
    result = lambda_handler({}, None)

    # Verify
    assert result["batch_id"] is None
    assert result["request_count"] == 0
    assert result["articles_count"] == 0
    assert "submitted_at" in result


@patch("rss_email.submit_email_batch.anthropic.Anthropic")
@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
def test_lambda_handler_large_batch(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    mock_env,
):
    """Test handler with articles requiring multiple batches."""
    # Create 50 articles to test batch splitting (25 per batch)
    articles = [
        {
            "title": f"Article {i}",
            "link": f"https://example.com/{i}",
            "description": f"Description {i}",
            "pubDate": "Mon, 29 Dec 2025 12:00:00 GMT",
        }
        for i in range(50)
    ]

    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = articles

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-456"
    mock_anthropic.return_value.messages.batches.create.return_value = mock_batch

    # Execute
    result = lambda_handler({}, None)

    # Verify
    assert result["batch_id"] == "batch-456"
    assert result["request_count"] == 2  # 50 articles split into 2 batches of 25
    assert result["articles_count"] == 50

    # Verify correct number of requests created
    call_args = mock_anthropic.return_value.messages.batches.create.call_args
    requests = call_args[1]["requests"]
    assert len(requests) == 2


@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.get_last_run")
def test_lambda_handler_ssm_error(
    mock_get_last_run, mock_boto3_client, mock_env
):
    """Test handler when SSM parameter retrieval fails."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = Exception("SSM error")
    mock_boto3_client.return_value = mock_ssm

    # Execute and verify exception is raised
    with pytest.raises(Exception, match="SSM error"):
        lambda_handler({}, None)


@patch("rss_email.submit_email_batch.anthropic.Anthropic")
@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
def test_lambda_handler_anthropic_api_error(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    mock_env,
    sample_articles,
):
    """Test handler when Anthropic API call fails."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_anthropic.return_value.messages.batches.create.side_effect = Exception(
        "API error"
    )

    # Execute and verify exception is raised
    with pytest.raises(Exception, match="API error"):
        lambda_handler({}, None)


def test_lambda_handler_missing_env_vars():
    """Test handler when environment variables are missing."""
    # Clear all env vars
    for key in ["RSS_BUCKET", "RSS_KEY", "LAST_RUN_PARAMETER",
                "ANTHROPIC_API_KEY_PARAMETER"]:
        if key in os.environ:
            del os.environ[key]

    # Execute and verify KeyError is raised
    with pytest.raises(KeyError):
        lambda_handler({}, None)
