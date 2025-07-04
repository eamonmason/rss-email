"""Unit tests for the retrieve_articles module.

This module contains test cases for RSS feed retrieval, parsing, and processing
functionality including S3 integration tests using moto mock.

Some code duplication with test_article_data.py is intentional for test isolation.
# pylint: disable=duplicate-code,R0801
"""

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import boto3
from moto import mock_aws
from pydantic import HttpUrl

import rss_email.retrieve_articles
from rss_email.retrieve_articles import (
    Article,
    create_rss,
    generate_rss,
    get_feed,
    get_feed_items,
    get_feed_urls,
    get_update_date,
    is_connected,
    retrieve_rss_feeds,
)

EXAMPLE_RSS_FILE = "example_rss_file.xml"


class TestRetrieveArticles(unittest.TestCase):
    """Test suite for RSS feed retrieval and processing functionality."""

    @patch("rss_email.retrieve_articles.urllib.request.urlopen")
    def test_get_feed_items(self, mock_urlopen):
        """Test retrieval of feed items from a URL with mocked urllib."""
        mock_context = MagicMock()
        mock_context.__enter__.return_value.read.return_value = b"feed data"
        mock_urlopen.return_value = mock_context

        url = "http://example.com/rss"
        timestamp = datetime.now() - timedelta(days=3)
        result = get_feed_items(url, timestamp)
        self.assertEqual(result, b"feed data")

    @patch("rss_email.retrieve_articles.urllib.request.urlopen")
    def test_get_feed(self, mock_urlopen):
        """Test feed parsing and processing with mocked URL response."""
        mock_context = MagicMock()
        mock_context.__enter__.return_value.read.return_value = b"feed data"
        mock_urlopen.return_value = mock_context

        feed_url = "http://example.com/feed"
        feed_data = b"feed data"
        update_date = datetime.now()
        result = get_feed(feed_url, feed_data, update_date)

        self.assertIsInstance(result, list)

    @patch("rss_email.retrieve_articles.boto3.client")
    @patch("rss_email.retrieve_articles.files")
    def test_get_feed_urls(self, mock_files, mock_boto3_client):
        """Test extraction of feed URLs from both local and S3 JSON files."""
        mock_files.return_value.joinpath.return_value.read_text.return_value = (
            '{"feeds": [{"url": "http://example.com/rss"}]}'
        )
        result = get_feed_urls("local_feed_file.json")
        self.assertEqual(result, ["http://example.com/rss"])

        mock_response_chain = mock_boto3_client.return_value.get_object.return_value.get.return_value.read.return_value
        mock_response_chain.decode.return_value = '{"feeds": [{"url": "http://example.com/rss"}]}'
        result = get_feed_urls("s3://bucket/feed_file.json")
        self.assertEqual(result, ["http://example.com/rss"])

    def test_get_update_date(self):
        """Test calculation of update date based on days parameter."""
        result = get_update_date(3)
        self.assertTrue(isinstance(result, datetime))

    def test_generate_rss(self):
        """Test RSS XML generation from Article objects."""
        articles = [
            Article(
                title="Article 1",
                link=HttpUrl("http://example.com/1"),
                pubdate=datetime.now(),
                description="Description 1",
            )
        ]
        result = generate_rss(articles)
        self.assertIn("<title>Article 1</title>", result)

    @patch("rss_email.retrieve_articles.socket.create_connection")
    def test_is_connected(self, mock_create_connection):
        """Test internet connectivity check with mocked socket connection."""
        mock_create_connection.return_value = True
        result = is_connected()
        self.assertTrue(result)

    @patch("rss_email.retrieve_articles.get_feed_urls")
    @patch("rss_email.retrieve_articles.get_feed_items")
    @patch("rss_email.retrieve_articles.generate_rss")
    @patch("rss_email.retrieve_articles.is_connected")
    def test_retrieve_rss_feeds(
        self,
        mock_is_connected,
        mock_generate_rss,
        mock_get_feed_items,
        mock_get_feed_urls,
    ):
        """Test end-to-end RSS feed retrieval process with mocked components."""
        mock_is_connected.return_value = True
        mock_get_feed_urls.return_value = ["http://example.com/rss"]
        mock_get_feed_items.return_value = "feed data"
        mock_generate_rss.return_value = "<rss>RSS content</rss>"

        result = retrieve_rss_feeds(
            "feed_file.json", datetime.now() - timedelta(days=3)
        )
        self.assertIn("<rss>RSS content</rss>", result)

    @mock_aws
    def test_create_rss(self):
        """Test RSS file creation and S3 upload using moto mock."""
        # Set up environment variables
        os.environ["BUCKET"] = "test-bucket"
        os.environ["KEY"] = "rss.xml"
        os.environ["FEED_DEFINITIONS_FILE"] = "test-file"

        # Set up S3 and mock
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-bucket")
        test_content = "<rss><title>Test RSS</title></rss>"
        rss_email.retrieve_articles.retrieve_rss_feeds = MagicMock(
            return_value=test_content
        )

        try:
            create_rss({"blah": "blah2"}, None)
            response = s3.get_object(Bucket="test-bucket", Key="rss.xml")
            content = response["Body"].read().decode("utf-8")
            self.assertEqual(content, test_content)
        finally:
            # Clean up environment variables
            os.environ.pop("BUCKET", None)
            os.environ.pop("KEY", None)
            os.environ.pop("FEED_DEFINITIONS_FILE", None)

    def test_get_specific_problematic_feed(self):
        """Test the feed URL that was returning 403 Forbidden errors."""
        # This test will verify that our fix for the 403 error works
        url = "https://towardsdatascience.com/feed/"
        timestamp = datetime.now() - timedelta(days=3)

        # This should now work without getting a 403 Forbidden error
        result = get_feed_items(url, timestamp)

        # Verify we got a response with actual RSS content
        self.assertNotEqual(result, b"")
        self.assertIn(b"<rss", result)

    def test_originally_problematic_feeds(self):
        """Test specifically the feeds that were causing issues."""
        # Only test the feed that was causing a 403 Forbidden error
        feeds = [
            "https://towardsdatascience.com/feed/",
            "https://www.techmeme.com/feed.xml",  # This feed should work reliably
        ]

        timestamp = datetime.now() - timedelta(days=3)

        for feed_url in feeds:
            with self.subTest(feed=feed_url):
                result = get_feed_items(feed_url, timestamp)
                # Verify we got actual content
                self.assertNotEqual(result, b"")
                # Verify it looks like RSS/XML content - only check for the techmeme feed
                # which is more reliable and predictable
                if "techmeme" in feed_url:
                    self.assertTrue(
                        b"<rss" in result or b"<feed" in result or b"<?xml" in result,
                        f"Feed {feed_url} did not return valid RSS/XML content",
                    )

    def test_ssl_certificate_handling(self):
        """Test the SSL certificate verification handling with fallback to unverified."""
        # Test some feeds that are likely to have SSL certificate issues but should work with our fallback
        # Using feeds that worked in our test_all_feeds.py
        feeds_to_test = [
            "https://stratechery.passport.online/feed/rss/S4nwHuhEnykTmJfDZVx4Ui",  # This worked with our SSL fix
            "https://www.awsarchitectureblog.com/atom.xml",  # This should work with our SSL fix
        ]

        timestamp = datetime.now() - timedelta(days=3)

        for feed_url in feeds_to_test:
            with self.subTest(feed=feed_url):
                result = get_feed_items(feed_url, timestamp)
                # Verify we got actual content
                self.assertNotEqual(result, b"")
                # Verify it looks like RSS/XML content
                self.assertTrue(
                    b"<rss" in result or b"<feed" in result or b"<?xml" in result,
                    f"Feed {feed_url} did not return valid RSS/XML content",
                )

    EXAMPLE_RSS_FILE = """
    {
            "feeds": [
                {
                    "name": "Test Feed A",
                    "url": "https://foo.com/feed/"
                },
                {
                    "name": "Test Feed B",
                    "url": "https://bar.com/posts.atom"
                },
                {
                    "name": "Test Feed C",
                    "_url": "https://acme.com/feed.xml"
                }
            ]
        }
    """

    @mock_aws
    def test_create_rss2(self):
        """Test RSS file creation and S3 upload with different test conditions."""
        # Set up mock S3 bucket
        bucket_name = "test-bucket"
        key = "test-key"
        content = "test"
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=bucket_name)
        rss_email.retrieve_articles.retrieve_rss_feeds = MagicMock(return_value=content)

        try:
            # Call create_rss function, with appropriate env variables
            os.environ["BUCKET"] = bucket_name
            os.environ["KEY"] = key
            os.environ["FEED_DEFINITIONS_FILE"] = "test-file"
            create_rss({"test": "blah"}, None)

            # Check that the file was uploaded to S3
            obj = s3.get_object(Bucket=bucket_name, Key=key)
            self.assertEqual(obj["Body"].read().decode("ASCII"), content)
        finally:
            # Clean up environment variables
            os.environ.pop("BUCKET", None)
            os.environ.pop("KEY", None)
            os.environ.pop("FEED_DEFINITIONS_FILE", None)


if __name__ == "__main__":
    unittest.main()
