"""Comprehensive end-to-end tests for the email articles flow."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from rss_email.email_articles import send_email, generate_html
from rss_email.article_processor import process_articles_with_claude, ClaudeRateLimiter


class TestComprehensiveEmailFlow(unittest.TestCase):
    """Comprehensive test cases for the complete email flow."""

    def setUp(self):
        """Set up test fixtures."""
        # Use current dates to ensure articles pass date filtering
        now = datetime.now()
        recent_date1 = now - timedelta(minutes=30)
        recent_date2 = now - timedelta(minutes=45)
        recent_date3 = now - timedelta(minutes=60)

        self.sample_rss = f"""<?xml version="1.0" encoding="UTF-8" ?>
            <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>AI Breakthrough: New Machine Learning Model</title>
                    <link>https://example.com/ai-breakthrough</link>
                    <description>New ML model shows promising NLP results.</description>
                    <pubDate>{recent_date1.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
                </item>
                <item>
                    <title>Cybersecurity Alert: New Vulnerability Discovered</title>
                    <link>https://example.com/security-alert</link>
                    <description>Critical vulnerability found in web frameworks.</description>
                    <pubDate>{recent_date2.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
                </item>
                <item>
                    <title>Tech News: Python 3.13 Released</title>
                    <link>https://example.com/python-release</link>
                    <description>Python latest version has performance improvements.</description>
                    <pubDate>{recent_date3.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate>
                </item>
            </channel>
            </rss>"""

        self.mock_claude_response = {
            "categories": {
                "AI/ML": [
                    {
                        "id": "article_0",
                        "title": "AI Breakthrough: New Machine Learning Model",
                        "link": "https://example.com/ai-breakthrough",
                        "summary": "Researchers developed a new ML model showing promising NLP results.",
                        "category": "AI/ML",
                        "pubdate": recent_date1.strftime('%a, %d %b %Y %H:%M:%S GMT'),
                        "related_articles": []
                    }
                ],
                "Cybersecurity": [
                    {
                        "id": "article_1",
                        "title": "Cybersecurity Alert: New Vulnerability Discovered",
                        "link": "https://example.com/security-alert",
                        "summary": "Critical vulnerability found in popular web frameworks by security researchers.",
                        "category": "Cybersecurity",
                        "pubdate": recent_date2.strftime('%a, %d %b %Y %H:%M:%S GMT'),
                        "related_articles": []
                    }
                ],
                "Technology": [
                    {
                        "id": "article_2",
                        "title": "Tech News: Python 3.13 Released",
                        "link": "https://example.com/python-release",
                        "summary": "Latest Python version includes performance improvements and new features.",
                        "category": "Technology",
                        "pubdate": recent_date3.strftime('%a, %d %b %Y %H:%M:%S GMT'),
                        "related_articles": []
                    }
                ]
            },
            "article_count": 3,
            "verification": "processed_all_articles"
        }

    @patch("rss_email.email_articles.boto3.client")
    @patch.dict(
        "os.environ",
        {
            "BUCKET": "test-bucket",
            "KEY": "test-key",
            "SOURCE_EMAIL_ADDRESS": "sender@example.com",
            "TO_EMAIL_ADDRESS": "recipient@example.com",
            "LAST_RUN_PARAMETER": "test-parameter",
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key"
        },
    )
    def test_full_email_flow_with_claude_success(self, mock_boto3_client):
        """Test the complete email flow with successful Claude processing."""
        # Mock SSM client with timestamp older than articles
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")}
        }

        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_s3_response = {"Body": MagicMock()}
        mock_s3_response["Body"].read.return_value = self.sample_rss.encode("utf-8")
        mock_s3_client.get_object.return_value = mock_s3_response

        # Mock SES client
        mock_ses_client = MagicMock()
        mock_ses_client.send_email.return_value = {"MessageId": "test-message-id"}

        # Configure boto3 client
        def mock_client(service_name):
            if service_name == "ssm":
                return mock_ssm_client
            if service_name == "ses":
                return mock_ses_client
            if service_name == "s3":
                return mock_s3_client
            return MagicMock()

        mock_boto3_client.side_effect = mock_client

        # Mock Claude API call
        with patch("rss_email.article_processor.anthropic.Anthropic") as mock_anthropic:
            mock_client_instance = MagicMock()
            mock_anthropic.return_value = mock_client_instance

            # Create mock response
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = json.dumps(self.mock_claude_response)
            mock_response.usage.input_tokens = 1000
            mock_response.usage.output_tokens = 500
            mock_client_instance.messages.create.return_value = mock_response

            # Create test event
            event = {
                "Records": [
                    {"Sns": {"Message": '{"mail": {"source": "recipient@example.com"}}'}}
                ]
            }

            # Call the function
            send_email(event, None)

            # Verify S3 was called
            mock_s3_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="test-key")

            # Verify Claude API was called
            mock_client_instance.messages.create.assert_called_once()

            # Verify email was sent
            mock_ses_client.send_email.assert_called_once()

            # Verify the email content includes Claude-processed content
            call_args = mock_ses_client.send_email.call_args
            email_body = call_args[1]["Message"]["Body"]["Html"]["Data"]
            self.assertIn("AI/ML", email_body)
            self.assertIn("Cybersecurity", email_body)
            self.assertIn("Technology", email_body)
            self.assertIn("AI Breakthrough", email_body)

    @patch("rss_email.email_articles.boto3.client")
    @patch.dict(
        "os.environ",
        {
            "BUCKET": "test-bucket",
            "KEY": "test-key",
            "SOURCE_EMAIL_ADDRESS": "sender@example.com",
            "TO_EMAIL_ADDRESS": "recipient@example.com",
            "LAST_RUN_PARAMETER": "test-parameter",
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key"
        },
    )
    def test_full_email_flow_with_claude_json_error_fallback(self, mock_boto3_client):
        """Test the complete email flow with Claude JSON errors and fallback."""
        # Mock SSM client with timestamp older than articles
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")}
        }

        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_s3_response = {"Body": MagicMock()}
        mock_s3_response["Body"].read.return_value = self.sample_rss.encode("utf-8")
        mock_s3_client.get_object.return_value = mock_s3_response

        # Mock SES client
        mock_ses_client = MagicMock()
        mock_ses_client.send_email.return_value = {"MessageId": "test-message-id"}

        # Configure boto3 client
        def mock_client(service_name):
            if service_name == "ssm":
                return mock_ssm_client
            if service_name == "ses":
                return mock_ses_client
            if service_name == "s3":
                return mock_s3_client
            return MagicMock()

        mock_boto3_client.side_effect = mock_client

        # Mock Claude API call with malformed JSON
        with patch("rss_email.article_processor.anthropic.Anthropic") as mock_anthropic:
            mock_client_instance = MagicMock()
            mock_anthropic.return_value = mock_client_instance

            # Create mock response with truly malformed JSON that can't be repaired
            malformed_json = '{"categories": {"AI/ML": [{"id": "article_0", "tit'
            mock_response = MagicMock()
            mock_response.content = [MagicMock()]
            mock_response.content[0].text = malformed_json
            mock_response.usage.input_tokens = 1000
            mock_response.usage.output_tokens = 500
            mock_client_instance.messages.create.return_value = mock_response

            # Create test event
            event = {
                "Records": [
                    {"Sns": {"Message": '{"mail": {"source": "recipient@example.com"}}'}}
                ]
            }

            # Call the function
            send_email(event, None)

            # Verify S3 was called
            mock_s3_client.get_object.assert_called_once()

            # Verify Claude API was called
            mock_client_instance.messages.create.assert_called_once()

            # Verify email was sent (fallback to original format)
            mock_ses_client.send_email.assert_called_once()

            # Verify the email content falls back to original format
            call_args = mock_ses_client.send_email.call_args
            email_body = call_args[1]["Message"]["Body"]["Html"]["Data"]
            # Should contain original format, not categorized format
            self.assertNotIn("AI/ML", email_body)
            self.assertIn("AI Breakthrough", email_body)  # Should still have articles

    @patch("rss_email.email_articles.boto3.client")
    @patch.dict(
        "os.environ",
        {
            "BUCKET": "test-bucket",
            "KEY": "test-key",
            "SOURCE_EMAIL_ADDRESS": "sender@example.com",
            "TO_EMAIL_ADDRESS": "recipient@example.com",
            "LAST_RUN_PARAMETER": "test-parameter",
            "CLAUDE_ENABLED": "false"
        },
    )
    def test_full_email_flow_with_claude_disabled(self, mock_boto3_client):
        """Test the complete email flow with Claude disabled."""
        # Mock SSM client with timestamp older than articles
        mock_ssm_client = MagicMock()
        mock_ssm_client.get_parameter.return_value = {
            "Parameter": {"Value": (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.%f")}
        }

        # Mock S3 client
        mock_s3_client = MagicMock()
        mock_s3_response = {"Body": MagicMock()}
        mock_s3_response["Body"].read.return_value = self.sample_rss.encode("utf-8")
        mock_s3_client.get_object.return_value = mock_s3_response

        # Mock SES client
        mock_ses_client = MagicMock()
        mock_ses_client.send_email.return_value = {"MessageId": "test-message-id"}

        # Configure boto3 client
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

        # Verify S3 was called
        mock_s3_client.get_object.assert_called_once()

        # Verify email was sent
        mock_ses_client.send_email.assert_called_once()

        # Verify the email content uses original format
        call_args = mock_ses_client.send_email.call_args
        email_body = call_args[1]["Message"]["Body"]["Html"]["Data"]
        self.assertNotIn("AI/ML", email_body)  # No categories
        self.assertIn("AI Breakthrough", email_body)  # Still has articles

    def test_generate_html_with_local_file(self):
        """Test HTML generation with local file instead of S3."""
        # Create a temporary RSS file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
            f.write(self.sample_rss)
            temp_file = f.name

        try:
            # Test with Claude disabled
            with patch.dict("os.environ", {"CLAUDE_ENABLED": "false"}):
                last_run_date = datetime.now() - timedelta(hours=2)
                html = generate_html(last_run_date, "dummy-bucket", "dummy-key", temp_file)

                # Verify HTML contains articles
                self.assertIn("AI Breakthrough", html)
                self.assertIn("Cybersecurity Alert", html)
                self.assertIn("Python 3.13", html)
                self.assertIn("Daily News", html)
        finally:
            os.unlink(temp_file)

    def test_claude_rate_limiter(self):
        """Test the Claude rate limiter functionality."""
        rate_limiter = ClaudeRateLimiter()

        # Test initial state
        self.assertTrue(rate_limiter.can_make_request(1000))
        self.assertEqual(rate_limiter.current_requests, 0)
        self.assertEqual(rate_limiter.current_tokens, 0)

        # Test recording usage
        rate_limiter.record_usage(1000)
        self.assertEqual(rate_limiter.current_requests, 1)
        self.assertEqual(rate_limiter.current_tokens, 1000)

        # Test usage stats
        stats = rate_limiter.get_usage_stats()
        self.assertEqual(stats["requests_made"], 1)
        self.assertEqual(stats["tokens_used"], 1000)
        self.assertGreater(stats["requests_remaining"], 0)
        self.assertGreater(stats["tokens_remaining"], 0)

    @patch("rss_email.article_processor.anthropic.Anthropic")
    def test_claude_processing_with_real_data(self, mock_anthropic):
        """Test Claude processing with actual RSS data structure."""
        # Create articles from the sample RSS
        articles = [
            {
                "title": "AI Breakthrough: New Machine Learning Model",
                "link": "https://example.com/ai-breakthrough",
                "description": "New ML model shows promising NLP results.",
                "pubDate": "Thu, 02 Jan 2025 12:00:00 GMT",
                "sortDate": 1735819200
            },
            {
                "title": "Cybersecurity Alert: New Vulnerability Discovered",
                "link": "https://example.com/security-alert",
                "description": "Critical vulnerability found in web frameworks.",
                "pubDate": "Thu, 02 Jan 2025 10:30:00 GMT",
                "sortDate": 1735813800
            }
        ]

        # Mock Claude client
        mock_client_instance = MagicMock()
        mock_anthropic.return_value = mock_client_instance

        # Mock successful response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = json.dumps(self.mock_claude_response)
        mock_response.usage.input_tokens = 1000
        mock_response.usage.output_tokens = 500
        mock_client_instance.messages.create.return_value = mock_response

        # Test with environment variables set
        with patch.dict("os.environ", {
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key"
        }):
            rate_limiter = ClaudeRateLimiter()
            result = process_articles_with_claude(articles, rate_limiter)

            # Verify processing was successful
            self.assertIsNotNone(result)
            self.assertIn("AI/ML", result.categories)
            self.assertIn("Cybersecurity", result.categories)
            self.assertEqual(len(result.categories["AI/ML"]), 1)
            self.assertEqual(len(result.categories["Cybersecurity"]), 1)

            # Verify rate limiter was updated
            self.assertEqual(rate_limiter.current_requests, 1)
            self.assertEqual(rate_limiter.current_tokens, 1500)

    @patch("rss_email.article_processor.anthropic.Anthropic")
    def test_claude_processing_with_json_repair(self, mock_anthropic):
        """Test Claude processing with JSON repair needed."""
        articles = [
            {
                "title": "Test Article",
                "link": "https://example.com/test",
                "description": "Test description",
                "pubDate": "Thu, 02 Jan 2025 12:00:00 GMT",
                "sortDate": 1735819200
            }
        ]

        # Mock Claude client
        mock_client_instance = MagicMock()
        mock_anthropic.return_value = mock_client_instance

        # Mock response with malformed JSON that can be repaired
        malformed_json = (
            '{"categories": {"Technology": [{"id": "article_0", "title": "Test Article", '
            '"link": "https://example.com/test" "summary": "Test", "category": "Technology", '
            '"pubdate": "Thu, 02 Jan 2025 12:00:00 GMT", "related_articles": []}]}, '
            '"article_count": 1, "verification": "processed_all_articles"}'
        )
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = malformed_json
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 300
        mock_client_instance.messages.create.return_value = mock_response

        # Test with environment variables set
        with patch.dict("os.environ", {
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key"
        }):
            rate_limiter = ClaudeRateLimiter()
            result = process_articles_with_claude(articles, rate_limiter)

            # Verify processing was successful despite JSON repair
            self.assertIsNotNone(result)
            self.assertIn("Technology", result.categories)
            self.assertEqual(len(result.categories["Technology"]), 1)
            self.assertEqual(result.categories["Technology"][0].title, "Test Article")


if __name__ == "__main__":
    unittest.main()
