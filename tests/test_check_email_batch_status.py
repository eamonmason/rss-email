"""Tests for check_email_batch_status module."""
# pylint: disable=redefined-outer-name,unused-argument

import os
from unittest.mock import MagicMock, patch

import pytest

from rss_email.check_email_batch_status import lambda_handler


@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables for testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")


@patch("rss_email.check_email_batch_status.anthropic.Anthropic")
@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_in_progress(
    mock_boto3_client, mock_anthropic, mock_env
):
    """Test handler when batch is still processing."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-123"
    mock_batch.processing_status = "in_progress"
    mock_batch.request_counts = MagicMock(
        processing=5,
        succeeded=3,
        errored=1,
        canceled=0,
        expired=0,
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "batch-123", "request_count": 10}
    result = lambda_handler(event, None)

    # Verify
    assert result["batch_id"] == "batch-123"
    assert result["processing_status"] == "in_progress"
    assert result["request_counts"]["processing"] == 5
    assert result["request_counts"]["succeeded"] == 3
    assert result["request_counts"]["errored"] == 1

    # Verify API calls
    mock_anthropic.return_value.messages.batches.retrieve.assert_called_once_with(
        "batch-123"
    )
    mock_ssm.get_parameter.assert_called_once_with(
        Name="test-api-key-param", WithDecryption=True
    )


@patch("rss_email.check_email_batch_status.anthropic.Anthropic")
@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_ended(
    mock_boto3_client, mock_anthropic, mock_env
):
    """Test handler when batch processing has ended."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-456"
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = MagicMock(
        processing=0,
        succeeded=10,
        errored=0,
        canceled=0,
        expired=0,
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "batch-456", "request_count": 10}
    result = lambda_handler(event, None)

    # Verify
    assert result["batch_id"] == "batch-456"
    assert result["processing_status"] == "ended"
    assert result["request_counts"]["processing"] == 0
    assert result["request_counts"]["succeeded"] == 10
    assert result["request_counts"]["errored"] == 0


@patch("rss_email.check_email_batch_status.anthropic.Anthropic")
@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_with_errors(
    mock_boto3_client, mock_anthropic, mock_env
):
    """Test handler when batch has some errors."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-789"
    mock_batch.processing_status = "ended"
    mock_batch.request_counts = MagicMock(
        processing=0,
        succeeded=8,
        errored=2,
        canceled=0,
        expired=0,
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "batch-789", "request_count": 10}
    result = lambda_handler(event, None)

    # Verify
    assert result["batch_id"] == "batch-789"
    assert result["processing_status"] == "ended"
    assert result["request_counts"]["succeeded"] == 8
    assert result["request_counts"]["errored"] == 2


def test_lambda_handler_null_batch_id(mock_env):
    """Test handler when batch_id is None (no articles to process)."""
    # Execute
    event = {"batch_id": None, "request_count": 0}
    result = lambda_handler(event, None)

    # Verify
    assert result["batch_id"] is None
    assert result["processing_status"] == "ended"
    assert result["request_counts"]["processing"] == 0
    assert result["request_counts"]["succeeded"] == 0


@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_ssm_error(mock_boto3_client, mock_env):
    """Test handler when SSM parameter retrieval fails."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = Exception("SSM error")
    mock_boto3_client.return_value = mock_ssm

    # Execute and verify exception is raised
    event = {"batch_id": "batch-123", "request_count": 10}
    with pytest.raises(Exception, match="SSM error"):
        lambda_handler(event, None)


@patch("rss_email.check_email_batch_status.anthropic.Anthropic")
@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_api_error(
    mock_boto3_client, mock_anthropic, mock_env
):
    """Test handler when Anthropic API call fails."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_anthropic.return_value.messages.batches.retrieve.side_effect = Exception(
        "API error"
    )

    # Execute and verify exception is raised
    event = {"batch_id": "batch-123", "request_count": 10}
    with pytest.raises(Exception, match="API error"):
        lambda_handler(event, None)


@patch("rss_email.check_email_batch_status.anthropic.Anthropic")
@patch("rss_email.check_email_batch_status.boto3.client")
def test_lambda_handler_canceling_status(
    mock_boto3_client, mock_anthropic, mock_env
):
    """Test handler when batch is being canceled."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    mock_batch = MagicMock()
    mock_batch.id = "batch-999"
    mock_batch.processing_status = "canceling"
    mock_batch.request_counts = MagicMock(
        processing=2,
        succeeded=5,
        errored=0,
        canceled=3,
        expired=0,
    )
    mock_anthropic.return_value.messages.batches.retrieve.return_value = mock_batch

    # Execute
    event = {"batch_id": "batch-999", "request_count": 10}
    result = lambda_handler(event, None)

    # Verify
    assert result["batch_id"] == "batch-999"
    assert result["processing_status"] == "canceling"
    assert result["request_counts"]["canceled"] == 3


def test_lambda_handler_missing_env_vars():
    """Test handler when environment variables are missing."""
    # Clear env var
    if "ANTHROPIC_API_KEY_PARAMETER" in os.environ:
        del os.environ["ANTHROPIC_API_KEY_PARAMETER"]

    # Execute and verify KeyError is raised
    event = {"batch_id": "batch-123", "request_count": 10}
    with pytest.raises(KeyError):
        lambda_handler(event, None)
