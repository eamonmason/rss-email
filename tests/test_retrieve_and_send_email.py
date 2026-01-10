"""Tests for retrieve_and_send_email module."""
# pylint: disable=redefined-outer-name,unused-argument,too-many-positional-arguments

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from rss_email.retrieve_and_send_email import lambda_handler, merge_categories


def test_merge_categories():
    """Test merging categories from multiple sources."""
    target = {
        "Technology": [
            {"title": "Article 1", "link": "https://example.com/1"}
        ],
        "AI/ML": [
            {"title": "Article 2", "link": "https://example.com/2"}
        ],
    }

    source = {
        "Technology": [
            {"title": "Article 3", "link": "https://example.com/3"}
        ],
        "Cybersecurity": [
            {"title": "Article 4", "link": "https://example.com/4"}
        ],
    }

    merge_categories(target, source)

    # Verify merge results
    assert len(target["Technology"]) == 2
    assert len(target["AI/ML"]) == 1
    assert len(target["Cybersecurity"]) == 1
    assert target["Technology"][1]["title"] == "Article 3"
    assert target["Cybersecurity"][0]["title"] == "Article 4"


@pytest.fixture
def mock_env(monkeypatch):
    """Set up environment variables for testing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY_PARAMETER", "test-api-key-param")
    monkeypatch.setenv("LAST_RUN_PARAMETER", "test-lastrun")
    monkeypatch.setenv("SOURCE_EMAIL_ADDRESS", "source@example.com")
    monkeypatch.setenv("TO_EMAIL_ADDRESS", "to@example.com")
    monkeypatch.setenv("RSS_BUCKET", "test-bucket")


@pytest.fixture
def sample_categorized_response():
    """Sample categorized response from Claude."""
    return {
        "categories": {
            "Technology": [
                {
                    "title": "Article 1",
                    "link": "https://example.com/1",
                    "summary": "Summary 1",
                }
            ],
            "AI/ML": [
                {
                    "title": "Article 2",
                    "link": "https://example.com/2",
                    "summary": "Summary 2",
                }
            ],
        }
    }


@patch("rss_email.retrieve_and_send_email.set_last_run")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.create_html")
@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_success(
    mock_boto3_client,
    mock_anthropic,
    mock_create_html,
    mock_send_via_ses,
    mock_set_last_run,
    mock_env,
    sample_categorized_response,
):
    """Test successful email retrieval and sending."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [
        MagicMock(text=json.dumps(sample_categorized_response))
    ]
    mock_result.custom_id = "email-batch-0"

    mock_anthropic.return_value.messages.batches.results.return_value = [
        mock_result
    ]

    mock_create_html.return_value = "<html>...</html>"

    # Execute
    event = {
        "batch_id": "batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    result = lambda_handler(event, None)

    # Verify
    assert result["status"] == "success"
    assert result["categories_count"] == 2
    assert result["failed_requests"] == 0

    # Verify email was sent
    mock_send_via_ses.assert_called_once_with(
        "to@example.com",
        "source@example.com",
        "Your Daily RSS Digest",
        "<html>...</html>",
    )

    # Verify last_run was updated
    mock_set_last_run.assert_called_once_with("test-lastrun")


@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_json_in_markdown(
    mock_boto3_client,
    mock_anthropic,
    mock_env,
    sample_categorized_response,
):
    """Test handling JSON wrapped in markdown code blocks."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with markdown-wrapped JSON
    json_str = json.dumps(sample_categorized_response)
    markdown_response = f"```json\n{json_str}\n```"

    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [MagicMock(text=markdown_response)]
    mock_result.custom_id = "email-batch-0"

    mock_anthropic.return_value.messages.batches.results.return_value = [
        mock_result
    ]

    with patch("rss_email.retrieve_and_send_email.create_html") as mock_create_html, \
         patch("rss_email.retrieve_and_send_email.send_via_ses"), \
         patch("rss_email.retrieve_and_send_email.set_last_run"):
        mock_create_html.return_value = "<html>...</html>"

        # Execute
        event = {
            "batch_id": "batch-123",
            "request_counts": {"succeeded": 1, "errored": 0},
        }
        result = lambda_handler(event, None)

        # Verify JSON was extracted successfully
        assert result["status"] == "success"
        assert result["categories_count"] == 2


@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_failed_request(
    mock_boto3_client,
    mock_anthropic,
    mock_env,
):
    """Test handling failed requests in batch results."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with failed request
    mock_result = MagicMock()
    mock_result.result.type = "errored"
    mock_result.custom_id = "email-batch-0"

    mock_anthropic.return_value.messages.batches.results.return_value = [
        mock_result
    ]

    with patch("rss_email.retrieve_and_send_email.create_html") as mock_create_html, \
         patch("rss_email.retrieve_and_send_email.send_via_ses"), \
         patch("rss_email.retrieve_and_send_email.set_last_run"):
        mock_create_html.return_value = "<html>...</html>"

        # Execute
        event = {
            "batch_id": "batch-123",
            "request_counts": {"succeeded": 0, "errored": 1},
        }
        result = lambda_handler(event, None)

        # Verify
        assert result["status"] == "success"
        assert result["categories_count"] == 0
        assert result["failed_requests"] == 1


def test_lambda_handler_null_batch_id(mock_env):
    """Test handler when batch_id is None (no articles to process)."""
    # Execute
    event = {
        "batch_id": None,
        "request_counts": {"succeeded": 0, "errored": 0},
    }
    result = lambda_handler(event, None)

    # Verify
    assert result["status"] == "success"
    assert result["categories_count"] == 0
    assert result["failed_requests"] == 0


@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_ssm_error(mock_boto3_client, mock_env):
    """Test handler when SSM parameter retrieval fails."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.side_effect = Exception("SSM error")
    mock_boto3_client.return_value = mock_ssm

    # Execute and verify exception is raised
    event = {
        "batch_id": "batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    with pytest.raises(Exception, match="SSM error"):
        lambda_handler(event, None)


@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_invalid_json(
    mock_boto3_client,
    mock_anthropic,
    mock_env,
):
    """Test handling invalid JSON in response."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result with invalid JSON
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [
        MagicMock(text="This is not valid JSON")
    ]
    mock_result.custom_id = "email-batch-0"

    mock_anthropic.return_value.messages.batches.results.return_value = [
        mock_result
    ]

    with patch("rss_email.retrieve_and_send_email.create_html") as mock_create_html, \
         patch("rss_email.retrieve_and_send_email.send_via_ses"), \
         patch("rss_email.retrieve_and_send_email.set_last_run"):
        mock_create_html.return_value = "<html>...</html>"

        # Execute
        event = {
            "batch_id": "batch-123",
            "request_counts": {"succeeded": 1, "errored": 0},
        }
        result = lambda_handler(event, None)

        # Verify - should treat as failed request
        assert result["status"] == "success"
        assert result["categories_count"] == 0
        assert result["failed_requests"] == 1


@patch("rss_email.retrieve_and_send_email.set_last_run")
@patch("rss_email.retrieve_and_send_email.send_via_ses")
@patch("rss_email.retrieve_and_send_email.create_html")
@patch("rss_email.retrieve_and_send_email.anthropic.Anthropic")
@patch("rss_email.retrieve_and_send_email.boto3.client")
def test_lambda_handler_no_categories_in_response(
    mock_boto3_client,
    mock_anthropic,
    mock_create_html,
    mock_send_via_ses,
    mock_set_last_run,
    mock_env,
):
    """Test handling response without categories field."""
    # Setup mocks
    mock_ssm = MagicMock()
    mock_ssm.get_parameter.return_value = {
        "Parameter": {"Value": "test-api-key"}
    }
    mock_boto3_client.return_value = mock_ssm

    # Mock batch result without categories
    mock_result = MagicMock()
    mock_result.result.type = "succeeded"
    mock_result.result.message.content = [
        MagicMock(text='{"some_other_field": "value"}')
    ]
    mock_result.custom_id = "email-batch-0"

    mock_anthropic.return_value.messages.batches.results.return_value = [
        mock_result
    ]

    mock_create_html.return_value = "<html>...</html>"

    # Execute
    event = {
        "batch_id": "batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    result = lambda_handler(event, None)

    # Verify - should still succeed but with no categories
    assert result["status"] == "success"
    assert result["categories_count"] == 0


def test_lambda_handler_missing_env_vars():
    """Test handler when environment variables are missing."""
    # Clear all env vars
    for key in [
        "ANTHROPIC_API_KEY_PARAMETER",
        "LAST_RUN_PARAMETER",
        "SOURCE_EMAIL_ADDRESS",
        "TO_EMAIL_ADDRESS",
    ]:
        if key in os.environ:
            del os.environ[key]

    # Execute and verify KeyError is raised
    event = {
        "batch_id": "batch-123",
        "request_counts": {"succeeded": 1, "errored": 0},
    }
    with pytest.raises(KeyError):
        lambda_handler(event, None)
