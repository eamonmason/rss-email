"""Tests for podcast batch processing modules."""
# pylint: disable=redefined-outer-name,unused-argument,too-many-positional-arguments

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from rss_email.submit_podcast_batch import lambda_handler as submit_handler
from rss_email.check_podcast_batch_status import lambda_handler as check_handler
from rss_email.retrieve_and_generate_podcast import lambda_handler as retrieve_handler


# ========== Submit Podcast Batch Tests ==========


@pytest.fixture
def submit_mock_env(monkeypatch):
    """Set up environment variables for submit_podcast_batch testing."""
    monkeypatch.setenv("RSS_BUCKET", "test-bucket")
    monkeypatch.setenv("RSS_KEY", "rss.xml")
    monkeypatch.setenv("PODCAST_LAST_RUN_PARAMETER", "test-podcast-lastrun")
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


@pytest.fixture
def sample_articles():
    """Sample articles for testing."""
    return [
        {
            "title": "Article 1",
            "description": "Description 1",
        },
        {
            "title": "Article 2",
            "description": "Description 2",
        },
    ]


@patch("rss_email.submit_podcast_batch.anthropic.Anthropic")
@patch("rss_email.submit_podcast_batch.boto3.client")
@patch("rss_email.submit_podcast_batch.filter_items")
@patch("rss_email.submit_podcast_batch.get_feed_file")
@patch("rss_email.submit_podcast_batch.get_last_run")
def test_submit_podcast_success(
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    mock_boto3_client,
    mock_anthropic,
    submit_mock_env,
    sample_articles,
):
    """Test successful podcast batch submission."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 26, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = sample_articles

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "podcast-batch-123"
    mock_anthropic.return_value.messages.batches.create.return_value = mock_batch

    # Execute
    result = submit_handler({}, None)

    # Verify
    assert result["batch_id"] == "podcast-batch-123"
    assert result["request_count"] == 1  # Single podcast script request
    assert result["articles_count"] == 2
    assert "submitted_at" in result


@patch("rss_email.submit_podcast_batch.filter_items")
@patch("rss_email.submit_podcast_batch.get_feed_file")
@patch("rss_email.submit_podcast_batch.get_last_run")
@patch("rss_email.submit_podcast_batch.boto3.client")
def test_submit_podcast_no_articles(
    mock_boto3_client,
    mock_get_last_run,
    mock_get_feed_file,
    mock_filter_items,
    submit_mock_env,
):
    """Test podcast submission when no articles to process."""
    # Setup mocks
    mock_get_last_run.return_value = datetime(2025, 12, 29, 0, 0, 0)
    mock_get_feed_file.return_value = "<rss>...</rss>"
    mock_filter_items.return_value = []

    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    # Execute
    result = submit_handler({}, None)

    # Verify
    assert result["batch_id"] is None
    assert result["request_count"] == 0
    assert result["articles_count"] == 0


# ========== Check Podcast Batch Status Tests ==========


@pytest.fixture
def check_mock_env(monkeypatch):
    """Set up environment variables for check_podcast_batch_status testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")


@patch("rss_email.check_podcast_batch_status.anthropic.Anthropic")
@patch("rss_email.check_podcast_batch_status.boto3.client")
def test_check_podcast_in_progress(
    mock_boto3_client, mock_anthropic, check_mock_env
):
    """Test checking podcast batch status when in progress."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "podcast-batch-123"
    mock_batch.processing_status = "in_progress"
    mock_batch.request_counts = MagicMock(
        processing=1, succeeded=0, errored=0, canceled=0, expired=0
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "podcast-batch-123", "request_count": 1}
    result = check_handler(event, None)

    # Verify
    assert result["batch_id"] == "podcast-batch-123"
    assert result["processing_status"] == "in_progress"
    assert result["request_counts"]["processing"] == 1


@patch("rss_email.check_podcast_batch_status.anthropic.Anthropic")
@patch("rss_email.check_podcast_batch_status.boto3.client")
def test_check_podcast_ended(mock_boto3_client, mock_anthropic, check_mock_env):
    """Test checking podcast batch status when ended."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "podcast-batch-456"
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = MagicMock(
        processing=0, succeeded=1, errored=0, canceled=0, expired=0
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "podcast-batch-456", "request_count": 1}
    result = check_handler(event, None)

    # Verify
    assert result["batch_id"] == "podcast-batch-456"
    assert result["processing_status"] == "ended"
    assert result["request_counts"]["succeeded"] == 1


def test_check_podcast_null_batch_id(check_mock_env):
    """Test checking podcast batch when batch_id is None."""
    # Execute
    event = {"batch_id": None, "request_count": 0}
    result = check_handler(event, None)

    # Verify
    assert result["batch_id"] is None
    assert result["processing_status"] == "ended"


# ========== Retrieve and Generate Podcast Tests ==========


@pytest.fixture
def retrieve_mock_env(monkeypatch):
    """Set up environment variables for retrieve_and_generate_podcast testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")
    monkeypatch.setenv("PODCAST_LAST_RUN_PARAMETER", "test-podcast-lastrun")
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("PODCAST_CLOUDFRONT_DISTRIBUTION_ID", "test-dist-id")
    monkeypatch.setenv("PODCAST_CLOUDFRONT_DOMAIN_PARAMETER", "test-domain-param")


@patch("rss_email.retrieve_and_generate_podcast.set_last_run")
@patch("rss_email.retrieve_and_generate_podcast.update_podcast_feed")
@patch("rss_email.retrieve_and_generate_podcast.upload_to_s3")
@patch("rss_email.retrieve_and_generate_podcast.synthesize_speech")
@patch("rss_email.retrieve_and_generate_podcast.anthropic.Anthropic")
@patch("rss_email.retrieve_and_generate_podcast.boto3.client")
def test_retrieve_podcast_success(
    mock_boto3_client,
    mock_anthropic,
    mock_synthesize,
    mock_upload,
    mock_update_feed,
    mock_set_last_run,
    retrieve_mock_env,
):
    """Test successful podcast retrieval and generation."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "test-api-key"}},  # API key
        {"Parameter": {"Value": "d123.cloudfront.net"}},  # CloudFront domain
    ]
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with podcast script
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [
        MagicMock(text="Marco: Welcome to the show!\nJoanna: Thanks for having me.")
    ]
    mock_anthropic.return_value.messages.batches.results.return_value = [mock_result]

    mock_synthesize.return_value = b"audio data"
    mock_upload.return_value = True
    mock_update_feed.return_value = True

    # Execute
    event = {
        "batch_id": "podcast-batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    result = retrieve_handler(event, None)

    # Verify
    assert result["status"] == "success"
    assert result["audio_generated"] is True
    assert "audio_url" in result
    assert "audio_size" in result

    # Verify functions were called
    mock_synthesize.assert_called_once()
    mock_upload.assert_called_once()
    mock_update_feed.assert_called_once()
    mock_set_last_run.assert_called_once()


def test_retrieve_podcast_null_batch_id(retrieve_mock_env):
    """Test retrieving podcast when batch_id is None."""
    # Execute
    event = {
        "batch_id": None,
        "request_counts": {"succeeded": 0, "errored": 0},
    }
    result = retrieve_handler(event, None)

    # Verify
    assert result["status"] == "success"
    assert result["audio_generated"] is False


@patch("rss_email.retrieve_and_generate_podcast.anthropic.Anthropic")
@patch("rss_email.retrieve_and_generate_podcast.boto3.client")
def test_retrieve_podcast_failed_batch(
    mock_boto3_client, mock_anthropic, retrieve_mock_env
):
    """Test retrieving podcast when batch processing failed."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-api-key"}}
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with failed request
    mock_result = MagicMock()
    mock_result.result.type = "errored"
    mock_anthropic.return_value.messages.batches.results.return_value = [mock_result]

    # Execute
    event = {
        "batch_id": "podcast-batch-123",
        "request_counts": {"succeeded": 0, "errored": 1},
    }
    result = retrieve_handler(event, None)

    # Verify
    assert result["status"] == "failed"
    assert result["audio_generated"] is False


@patch("rss_email.retrieve_and_generate_podcast.synthesize_speech")
@patch("rss_email.retrieve_and_generate_podcast.anthropic.Anthropic")
@patch("rss_email.retrieve_and_generate_podcast.boto3.client")
def test_retrieve_podcast_synthesis_failure(
    mock_boto3_client,
    mock_anthropic,
    mock_synthesize,
    retrieve_mock_env,
):
    """Test retrieving podcast when speech synthesis fails."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "test-api-key"}},
        {"Parameter": {"Value": "d123.cloudfront.net"}},
    ]
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with script
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [MagicMock(text="Marco: Hello!")]
    mock_anthropic.return_value.messages.batches.results.return_value = [mock_result]

    mock_synthesize.return_value = None  # Synthesis fails

    # Execute
    event = {
        "batch_id": "podcast-batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    result = retrieve_handler(event, None)

    # Verify
    assert result["status"] == "failed"
    assert result["audio_generated"] is False


@patch("rss_email.retrieve_and_generate_podcast.update_podcast_feed")
@patch("rss_email.retrieve_and_generate_podcast.upload_to_s3")
@patch("rss_email.retrieve_and_generate_podcast.synthesize_speech")
@patch("rss_email.retrieve_and_generate_podcast.anthropic.Anthropic")
@patch("rss_email.retrieve_and_generate_podcast.boto3.client")
def test_retrieve_podcast_upload_failure(
    mock_boto3_client,
    mock_anthropic,
    mock_synthesize,
    mock_upload,
    mock_update_feed,
    retrieve_mock_env,
):
    """Test retrieving podcast when S3 upload fails."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "test-api-key"}},
        {"Parameter": {"Value": "d123.cloudfront.net"}},
    ]
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with script
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [MagicMock(text="Marco: Hello!")]
    mock_anthropic.return_value.messages.batches.results.return_value = [mock_result]

    mock_synthesize.return_value = b"audio data"
    mock_upload.return_value = False  # Upload fails

    # Execute
    event = {
        "batch_id": "podcast-batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    result = retrieve_handler(event, None)

    # Verify
    assert result["status"] == "failed"
    assert result["audio_generated"] is True  # Audio was generated but upload failed
