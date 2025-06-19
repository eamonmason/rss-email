"""Unit tests for the email_articles module."""

# pylint: disable=duplicate-code

# Rename this file to test_email_articles.py to ensure pytest discovers it

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from rss_email.email_articles import send_email


class TestEmailArticles(unittest.TestCase):
    """Test cases for the email_articles module's send_email function."""

    @patch("rss_email.email_articles.boto3.client")
    @patch.dict(
        "os.environ",
        {
            "BUCKET": "test-bucket",
            "KEY": "test-key",
            "SOURCE_EMAIL_ADDRESS": "sender@example.com",
            "TO_EMAIL_ADDRESS": "recipient@example.com",
            "LAST_RUN_PARAMETER": "test-parameter",
        },
    )
    def test_send_email(self, mock_boto3_client):
        """
        Test the send_email function with mocked AWS services.

        Tests that the function correctly processes RSS content and sends an email
        using mocked S3, SSM, and SES services.

        Args:
            mock_boto3_client: Mocked boto3 client for AWS services
        """
        # Mock SSM client and parameter response
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")}
        }

        # Mock S3 client with sample RSS content
        mock_s3_client = MagicMock()
        sample_rss = """<?xml version="1.0" encoding="UTF-8" ?>
            <rss version="2.0">
            <channel>
                <item>
                    <title>Test Article</title>
                    <link>http://example.com</link>
                    <description>Test Description</description>
                    <pubDate>Thu, 23 Nov 2023 12:00:00 GMT</pubDate>
                </item>
            </channel>
            </rss>"""
        mock_s3_response = {"Body": MagicMock()}
        mock_s3_response["Body"].read.return_value = sample_rss.encode("utf-8")
        mock_s3_client.get_object.return_value = mock_s3_response

        # Mock SES client
        mock_ses_client = MagicMock()

        # Configure boto3 client to return appropriate mock for each service
        def mock_client(service_name):
            if service_name == "ssm":
                return mock_ssm_client
            if service_name == "ses":
                return mock_ses_client
            if service_name == "s3":
                return mock_s3_client
            return MagicMock()

        mock_boto3_client.side_effect = mock_client

        # Create test event
        event = {
            "Records": [
                {"Sns": {"Message": '{"mail": {"source": "recipient@example.com"}}'}}
            ]
        }

        # Call the function
        send_email(event, None)

        # Verify send_email was called
        mock_ses_client.send_email.assert_called_once()


if __name__ == "__main__":
    unittest.main()
