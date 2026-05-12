"""Unit tests for the email_articles module."""

# pylint: disable=duplicate-code

# Rename this file to test_email_articles.py to ensure pytest discovers it

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from rss_email.email_articles import generate_enhanced_html_content, send_email
from rss_email.article_processor import ProcessedArticle
from rss_email.models import ArticleSource


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


class TestSourceRendering(unittest.TestCase):
    """The new sources block replaces the old 'Related:' footer."""

    def test_singleton_source_shows_single_source_line(self):
        """One source ⇒ 'Source: <feed>' line."""
        article = ProcessedArticle(
            title="Solo Story",
            link="https://example.com/a",
            summary="A summary.",
            category="Technology",
            pubdate="Mon, 12 May 2026 10:00:00 GMT",
            sources=[
                ArticleSource(
                    feed_name="Feed A",
                    feed_url="https://a.example/feed/",
                    title="Solo Story",
                    link="https://example.com/a",
                    pubdate="Mon, 12 May 2026 10:00:00 GMT",
                )
            ],
        )

        html = generate_enhanced_html_content([("Technology", [article])])

        self.assertIn("Source:", html)
        self.assertNotIn("Also covered by", html)
        self.assertIn(">Feed A</a>", html)

    def test_multiple_sources_show_also_covered_by(self):
        """Multi-source group renders 'Also covered by:' with one link per feed."""
        article = ProcessedArticle(
            title="Shared Story",
            link="https://example.com/shared-a",
            summary="Shared.",
            category="AI/ML",
            pubdate="Mon, 12 May 2026 10:00:00 GMT",
            sources=[
                ArticleSource(
                    feed_name="Feed A",
                    feed_url="https://a.example/feed/",
                    title="Shared Story (A)",
                    link="https://example.com/shared-a",
                    pubdate="Mon, 12 May 2026 10:00:00 GMT",
                ),
                ArticleSource(
                    feed_name="Feed B",
                    feed_url="https://b.example/feed/",
                    title="Shared Story (B)",
                    link="https://example.com/shared-b",
                    pubdate="Mon, 12 May 2026 10:10:00 GMT",
                ),
                ArticleSource(
                    feed_name="Feed C",
                    feed_url="https://c.example/feed/",
                    title="Shared Story (C)",
                    link="https://example.com/shared-c",
                    pubdate="Mon, 12 May 2026 10:20:00 GMT",
                ),
            ],
        )

        html = generate_enhanced_html_content([("AI/ML", [article])])

        self.assertIn("Also covered by:", html)
        self.assertNotIn("Related:", html)
        for link in (
            "https://example.com/shared-a",
            "https://example.com/shared-b",
            "https://example.com/shared-c",
        ):
            self.assertIn(link, html)

    def test_no_related_footer_anywhere(self):
        """The old 'Related:' bordered footer block is gone from rendering."""
        article = ProcessedArticle(
            title="No Related",
            link="https://example.com/x",
            summary="X.",
            category="Other",
            pubdate="Mon, 12 May 2026 10:00:00 GMT",
            sources=[],
        )

        html = generate_enhanced_html_content([("Other", [article])])

        self.assertNotIn("Related:", html)


if __name__ == "__main__":
    unittest.main()
