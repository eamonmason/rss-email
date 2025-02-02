import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from rss_email.retrieve_articles import (
    generate_rss,
    get_feed,
    get_feed_items,
    get_feed_urls,
    get_update_date,
    is_connected,
    retrieve_rss_feeds,
)


class TestRetrieveArticles(unittest.TestCase):
    @patch("rss_email.retrieve_articles.urllib.request.urlopen")
    def test_get_feed_items(self, mock_urlopen):
        mock_context = MagicMock()
        mock_context.__enter__.return_value.read.return_value = b"feed data"
        mock_urlopen.return_value = mock_context

        url = "http://example.com/rss"
        timestamp = datetime.now() - timedelta(days=3)
        result = get_feed_items(url, timestamp)
        self.assertEqual(result, b"feed data")

    @patch("rss_email.retrieve_articles.urllib.request.urlopen")
    def test_get_feed(self, mock_urlopen):
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
        mock_files.return_value.joinpath.return_value.read_text.return_value = (
            '{"feeds": [{"url": "http://example.com/rss"}]}'
        )
        result = get_feed_urls("local_feed_file.json")
        self.assertEqual(result, ["http://example.com/rss"])

        mock_boto3_client.return_value.get_object.return_value.get.return_value.read.return_value.decode.return_value = '{"feeds": [{"url": "http://example.com/rss"}]}'
        result = get_feed_urls("s3://bucket/feed_file.json")
        self.assertEqual(result, ["http://example.com/rss"])

    def test_get_update_date(self):
        result = get_update_date(3)
        self.assertTrue(isinstance(result, datetime))

    def test_generate_rss(self):
        articles = [
            {
                "title": "Article 1",
                "link": "http://example.com/1",
                "pubdate": datetime.now(),
                "description": "Description 1",
            }
        ]
        result = generate_rss(articles)
        self.assertIn("<title>Article 1</title>", result)

    @patch("rss_email.retrieve_articles.socket.create_connection")
    def test_is_connected(self, mock_create_connection):
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
        mock_is_connected.return_value = True
        mock_get_feed_urls.return_value = ["http://example.com/rss"]
        mock_get_feed_items.return_value = "feed data"
        mock_generate_rss.return_value = "<rss>RSS content</rss>"

        result = retrieve_rss_feeds(
            "feed_file.json", datetime.now() - timedelta(days=3)
        )
        self.assertIn("<rss>RSS content</rss>", result)


if __name__ == "__main__":
    unittest.main()
