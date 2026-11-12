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
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5")
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


@patch("rss_email.submit_email_batch.build_groups_for_articles")
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
    mock_build_groups,
    mock_env,
    sample_articles,
):
    """Test successful batch submission with articles."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles
    mock_build_groups.return_value = [[0], [1]]

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
    # poll_count seeds the Step Functions poll-backoff loop at 0
    assert result["poll_count"] == 0

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


@patch("rss_email.submit_email_batch.build_groups_for_articles")
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
    mock_build_groups,
    mock_env,
):
    """Test handler with groups requiring multiple batch requests."""
    # Create 50 articles - each gets its own singleton group, so 50 groups -> 2 batches of 25
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
    mock_build_groups.return_value = [[i] for i in range(50)]

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
    assert result["request_count"] == 2  # 50 groups split into 2 batches of 25
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


@patch("rss_email.submit_email_batch.build_groups_for_articles")
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
    mock_build_groups,
    mock_env,
    sample_articles,
):
    """Test handler when Anthropic API call fails."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles
    mock_build_groups.return_value = [[0], [1]]

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


def _bad_request(message):
    import anthropic  # pylint: disable=import-outside-toplevel
    import httpx2  # pylint: disable=import-outside-toplevel

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    response = httpx2.Response(400, request=request)
    return anthropic.BadRequestError(message, response=response, body=None)


@patch("rss_email.submit_email_batch.build_groups_for_articles")
@patch("rss_email.submit_email_batch.anthropic.Anthropic")
@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
def test_batch_rejecting_effort_is_retried_without_it(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    mock_build_groups,
    mock_env,
    sample_articles,
    monkeypatch,
):
    """If the Batch API refuses output_config for Haiku 5, the day's batch is still submitted."""
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles
    mock_build_groups.return_value = [[0], [1]]
    mock_boto3_client.return_value.get_parameter.return_value = {"Parameter": {"Value": "k"}}
    accepted = MagicMock()
    accepted.id = "batch-ok"
    create = mock_anthropic.return_value.messages.batches.create
    create.side_effect = [_bad_request("requests.0.params.output_config: not permitted"), accepted]

    result = lambda_handler({}, None)

    assert result["batch_id"] == "batch-ok" and create.call_count == 2
    first, second = (call.kwargs["requests"][0]["params"] for call in create.call_args_list)
    assert first["output_config"] == {"effort": "low"}
    assert "output_config" not in second


@patch("rss_email.submit_email_batch.build_groups_for_articles")
@patch("rss_email.submit_email_batch.anthropic.Anthropic")
@patch("rss_email.submit_email_batch.boto3.client")
@patch("rss_email.submit_email_batch.filter_items")
@patch("rss_email.submit_email_batch.get_feed_file")
@patch("rss_email.submit_email_batch.get_last_run")
def test_unrelated_bad_request_is_not_retried(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    mock_build_groups,
    mock_env,
    sample_articles,
    monkeypatch,
):
    """Only an effort rejection triggers the retry; other 400s still surface."""
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-5-5")
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles
    mock_build_groups.return_value = [[0], [1]]
    mock_boto3_client.return_value.get_parameter.return_value = {"Parameter": {"Value": "k"}}
    create = mock_anthropic.return_value.messages.batches.create
    create.side_effect = _bad_request("Your credit balance is too low")

    with pytest.raises(Exception, match="credit balance"):
        lambda_handler({}, None)
    assert create.call_count == 1


def test_batch_prompt_version_defaults_to_v1_and_can_be_switched(monkeypatch):
    """Production keeps the original prompt until v2 passes its gates; an env var switches it."""
    from rss_email.submit_email_batch import create_batch_requests  # pylint: disable=import-outside-toplevel

    articles = [{"title": "T", "description": "d", "link": "https://x/1", "pubDate": "now"}]
    batches = [[("group_0", [0])]]
    monkeypatch.delenv("CATEGORY_PROMPT_VERSION", raising=False)
    old = create_batch_requests(batches, articles, "claude-haiku-4-5")[0]["params"]["messages"][0]["content"]
    assert "in priority order" in old and "HOW TO CHOOSE" not in old
    monkeypatch.setenv("CATEGORY_PROMPT_VERSION", "v2")
    new = create_batch_requests(batches, articles, "claude-haiku-4-5")[0]["params"]["messages"][0]["content"]
    assert "HOW TO CHOOSE A CATEGORY" in new
