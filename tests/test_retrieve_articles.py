"""Unit tests for the retrieve_articles module.

This module contains test cases for RSS feed retrieval, parsing, and processing
functionality.

Some code duplication with test_article_data.py is intentional for test isolation.
# pylint: disable=duplicate-code,R0801
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
from pydantic import HttpUrl

import rss_email.retrieve_articles
from rss_email.models import FeedConfig
from rss_email.retrieve_articles import (
    Article,
    generate_articles_json,
    get_feed,
    get_feed_items,
    get_feed_limits,
    get_feed_urls,
    get_update_date,
    is_connected,
    retrieve_rss_feeds,
    _rate_limited_host_key,
    _throttle_host,
)


class TestRetrieveArticles(unittest.TestCase):
    """Test suite for RSS feed retrieval and processing functionality."""

    @patch("rss_email.retrieve_articles.httpx.get")
    def test_get_feed_items(self, mock_get):
        """Test retrieval of feed items from a URL with mocked httpx."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"feed data"
        mock_get.return_value = mock_response

        url = "http://example.com/rss"
        timestamp = datetime.now() - timedelta(days=3)
        result = get_feed_items(url, timestamp)
        self.assertEqual(result, b"feed data")

    def test_rate_limited_host_key(self):
        """Reddit hosts map to the throttle key; unrelated hosts do not."""
        self.assertEqual(
            _rate_limited_host_key("https://www.reddit.com/r/aws/top/.rss?t=month"),
            "reddit.com",
        )
        self.assertEqual(
            _rate_limited_host_key("https://old.reddit.com/r/aws/.rss"), "reddit.com"
        )
        self.assertIsNone(_rate_limited_host_key("https://example.com/feed"))
        # A look-alike host must not match.
        self.assertIsNone(_rate_limited_host_key("https://notreddit.com.evil.test/x"))

    @patch("rss_email.retrieve_articles.time.sleep")
    def test_throttle_spaces_requests(self, mock_sleep):
        """A second request to a rate-limited host waits out the interval."""
        # pylint: disable=protected-access
        rss_email.retrieve_articles._host_last_start.clear()
        self.addCleanup(rss_email.retrieve_articles._host_last_start.clear)
        url = "https://www.reddit.com/r/aws/top/.rss?t=month"

        # First call records the start time and must not sleep.
        _throttle_host(url)
        mock_sleep.assert_not_called()

        # Second call (immediately after) must wait close to the configured interval.
        _throttle_host(url)
        mock_sleep.assert_called_once()
        waited = mock_sleep.call_args[0][0]
        interval = rss_email.retrieve_articles.RATE_LIMITED_HOSTS["reddit.com"]
        self.assertGreater(waited, 0)
        self.assertLessEqual(waited, interval)

    @patch("rss_email.retrieve_articles.time.sleep")
    @patch("rss_email.retrieve_articles.httpx.get")
    def test_get_feed_items_429_skips_without_retry(self, mock_get, mock_sleep):
        """A 429 is skipped without retrying or firing fallback requests."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_get.return_value = mock_response

        url = "https://example.com/rss"
        timestamp = datetime.now() - timedelta(days=3)
        result = get_feed_items(url, timestamp)

        # No content, a single request (no retry), no backoff sleep.
        self.assertEqual(result, b"")
        self.assertEqual(mock_get.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("rss_email.retrieve_articles.httpx.get")
    def test_get_feed_items_403_logged_at_info_not_warning(self, mock_get):
        """A 403 is expected/routine for scraper-blocking sites.

        It must not log at WARNING (which feeds the ErrorWarningCount metric
        filter and pages on-call for a single blocked feed), matching the
        429 treatment which was already downgraded for the same reason.
        """
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_get.return_value = mock_response

        url = "https://example.com/rss"
        timestamp = datetime.now() - timedelta(days=3)

        with self.assertLogs("rss_email.retrieve_articles", level="INFO") as captured:
            get_feed_items(url, timestamp)

        self.assertTrue(any("403" in message for message in captured.output))
        self.assertFalse(any(record.levelname == "WARNING" for record in captured.records))

    def test_get_feed(self):
        """Test feed parsing and processing from raw feed bytes."""
        feed_url = "http://example.com/feed"
        feed_data = b"feed data"
        update_date = datetime.now()
        result = get_feed(feed_url, feed_data, update_date)

        self.assertIsInstance(result, list)

    def test_get_feed_skips_undated_entry_but_keeps_later_ones(self):
        """An entry with no published/updated date must not truncate the feed.

        get_feed() used to `break` on the first entry lacking a parseable
        date, discarding every entry after it in document order. It should
        skip just that one entry and keep processing the rest.
        """
        rss_data = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <item>
                <title>Dated First</title>
                <link>http://example.com/first</link>
                <pubDate>Mon, 12 May 2025 10:00:00 GMT</pubDate>
            </item>
            <item>
                <title>Undated Middle</title>
                <link>http://example.com/middle</link>
            </item>
            <item>
                <title>Dated Last</title>
                <link>http://example.com/last</link>
                <pubDate>Mon, 12 May 2025 12:00:00 GMT</pubDate>
            </item>
        </channel>
        </rss>"""

        update_date = datetime(2025, 1, 1)
        result = get_feed("http://example.com/feed", rss_data, update_date)

        titles = [article.title for article in result]
        self.assertIn("Dated First", titles)
        self.assertIn("Dated Last", titles)
        self.assertNotIn("Undated Middle", titles)

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

    def test_generate_articles_json(self):
        """Test JSON generation from Article objects."""
        articles = [
            Article(
                title="Article 1",
                link=HttpUrl("http://example.com/1"),
                pubdate=datetime.now(),
                description="Description 1",
            )
        ]
        result = generate_articles_json(articles)
        parsed = json.loads(result)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["title"], "Article 1")

    def test_generate_articles_json_emits_source_fields(self):
        """Articles with source_name/source_url surface as sourceName/sourceUrl."""
        articles = [
            Article(
                title="Article 1",
                link=HttpUrl("http://example.com/1"),
                pubdate=datetime.now(),
                description="Description 1",
                source_name="Krebs on Security",
                source_url=HttpUrl("https://krebsonsecurity.com/feed/"),
            )
        ]

        result = generate_articles_json(articles)
        parsed = json.loads(result)

        self.assertEqual(parsed[0]["sourceUrl"], "https://krebsonsecurity.com/feed/")
        self.assertEqual(parsed[0]["sourceName"], "Krebs on Security")

    def test_source_round_trip_via_filter_items(self):
        """generate_articles_json -> filter_items preserves sourceName/sourceUrl."""
        # Local import to avoid load-time coupling with retrieve_articles tests
        # pylint: disable=import-outside-toplevel
        from rss_email.email_articles import filter_items

        pub = datetime.now() - timedelta(minutes=30)
        articles = [
            Article(
                title="Round Trip",
                link=HttpUrl("http://example.com/rt"),
                pubdate=pub,
                description="d",
                source_name="Feed X",
                source_url=HttpUrl("https://x.example/feed/"),
            )
        ]
        articles_json = generate_articles_json(articles)

        items = filter_items(articles_json, datetime.now() - timedelta(hours=2))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["sourceName"], "Feed X")
        self.assertEqual(items[0]["sourceUrl"], "https://x.example/feed/")

    @patch("rss_email.retrieve_articles.socket.create_connection")
    def test_is_connected(self, mock_create_connection):
        """Test internet connectivity check with mocked socket connection."""
        mock_create_connection.return_value = True
        result = is_connected()
        self.assertTrue(result)

    @patch("rss_email.retrieve_articles.get_feed_urls")
    @patch("rss_email.retrieve_articles.get_feed_items")
    @patch("rss_email.retrieve_articles.generate_articles_json")
    @patch("rss_email.retrieve_articles.is_connected")
    def test_retrieve_rss_feeds(
        self,
        mock_is_connected,
        mock_generate_articles_json,
        mock_get_feed_items,
        mock_get_feed_urls,
    ):
        """Test end-to-end RSS feed retrieval process with mocked components."""
        mock_is_connected.return_value = True
        mock_get_feed_urls.return_value = ["http://example.com/rss"]
        mock_get_feed_items.return_value = "feed data"
        mock_generate_articles_json.return_value = '[{"title": "Article"}]'

        content, counts = retrieve_rss_feeds(
            "feed_file.json", datetime.now() - timedelta(days=3)
        )
        self.assertIn('[{"title": "Article"}]', content)
        self.assertIsInstance(counts, dict)

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

    @patch("rss_email.retrieve_articles.time.sleep")
    @patch("rss_email.retrieve_articles.httpx.get")
    def test_get_feed_items_retries_transient_errors(self, mock_get, mock_sleep):
        """A transient connection error is retried, and a later success is returned.

        httpx auto-decodes gzip/deflate/br based on Content-Encoding, so unlike
        the old urllib implementation there is no separate SSL/HTTP-downgrade
        fallback path left to test here.
        """
        success_response = MagicMock()
        success_response.status_code = 200
        success_response.content = b"<?xml version='1.0'?><rss><channel/></rss>"

        mock_get.side_effect = [
            httpx.ConnectError("connection reset"),
            success_response,
        ]

        timestamp = datetime.now() - timedelta(days=3)
        result = get_feed_items("https://example.com/feed.xml", timestamp)

        self.assertIn(b"<rss", result)
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep.assert_called_once()


class TestFeedParseIsolation(unittest.TestCase):
    """Tests that a single feed failure does not abort the whole run."""

    @patch("rss_email.retrieve_articles.get_feed_urls")
    @patch("rss_email.retrieve_articles.get_feed_items")
    @patch("rss_email.retrieve_articles.is_connected")
    def test_feed_parse_failure_does_not_fail_run(
        self, mock_connected, mock_get_items, mock_get_urls
    ):
        """A get_feed() exception on one URL must not prevent other feeds from processing."""
        mock_connected.return_value = True
        mock_get_urls.return_value = [
            "http://good.example/feed",
            "http://bad.example/feed",
        ]
        mock_get_items.return_value = b"<rss/>"

        def side_effect_get_feed(url, *_, **__):
            if "bad" in url:
                raise ValueError("simulated parse failure")
            return []

        with patch("rss_email.retrieve_articles.get_feed", side_effect=side_effect_get_feed):
            _, counts = retrieve_rss_feeds(
                "feed_file.json", datetime.now() - timedelta(days=3)
            )

        self.assertEqual(counts.get("http://bad.example/feed", 0), 0)
        self.assertIn("http://good.example/feed", counts)


class TestFeedLimits(unittest.TestCase):
    """Tests for per-feed max_articles and lookback_days config."""

    def test_feed_config_parses_limits(self):
        """FeedConfig.from_dict() correctly parses max_articles and lookback_days."""
        cfg = FeedConfig.from_dict(
            {"name": "Test", "url": "https://example.com/feed/", "max_articles": 20, "lookback_days": 1}
        )
        self.assertEqual(cfg.max_articles, 20)
        self.assertEqual(cfg.lookback_days, 1)

    def test_feed_config_limits_default_to_none(self):
        """max_articles and lookback_days default to None when omitted."""
        cfg = FeedConfig.from_dict({"name": "Test", "url": "https://example.com/feed/"})
        self.assertIsNone(cfg.max_articles)
        self.assertIsNone(cfg.lookback_days)

    @patch("rss_email.retrieve_articles.files")
    def test_get_feed_limits_returns_per_url_dict(self, mock_files):
        """get_feed_limits() returns correct per-URL dict from feed JSON."""
        mock_files.return_value.joinpath.return_value.read_text.return_value = (
            '{"feeds": [{"name": "A", "url": "https://a.com/feed/", "max_articles": 10, "lookback_days": 2},'
            ' {"name": "B", "url": "https://b.com/feed/"}]}'
        )
        limits = get_feed_limits("local.json")
        self.assertEqual(limits["https://a.com/feed/"]["max_articles"], 10)
        self.assertEqual(limits["https://a.com/feed/"]["lookback_days"], 2)
        self.assertIsNone(limits["https://b.com/feed/"]["max_articles"])
        self.assertIsNone(limits["https://b.com/feed/"]["lookback_days"])

    @patch("rss_email.retrieve_articles.get_feed")
    @patch("rss_email.retrieve_articles.get_feed_items")
    @patch("rss_email.retrieve_articles.get_feed_urls")
    @patch("rss_email.retrieve_articles.is_connected")
    def test_retrieve_applies_max_articles_cap(
        self, mock_connected, mock_urls, mock_items, mock_get_feed
    ):
        """retrieve_rss_feeds() caps per-feed articles when max_articles is set."""
        mock_connected.return_value = True
        url = "https://example.com/feed/"
        mock_urls.return_value = [url]
        mock_items.return_value = b"<rss/>"
        articles = [
            Article(
                title=f"Article {i}",
                link=f"https://example.com/{i}",
                pubdate=datetime.now() - timedelta(hours=i),
                description="",
            )
            for i in range(30)
        ]
        mock_get_feed.return_value = articles

        limits = {url: {"max_articles": 5, "lookback_days": None}}
        with patch("rss_email.retrieve_articles.get_feed_limits", return_value=limits):
            _, counts = retrieve_rss_feeds("feed.json", datetime.now() - timedelta(days=3))
        self.assertEqual(counts[url], 5)

    @patch("rss_email.retrieve_articles.get_feed")
    @patch("rss_email.retrieve_articles.get_feed_items")
    @patch("rss_email.retrieve_articles.get_feed_urls")
    @patch("rss_email.retrieve_articles.is_connected")
    def test_retrieve_uses_per_feed_lookback(
        self, mock_connected, mock_urls, mock_items, mock_get_feed
    ):
        """retrieve_rss_feeds() passes a feed-specific update_date when lookback_days is set."""
        mock_connected.return_value = True
        url = "https://example.com/feed/"
        mock_urls.return_value = [url]
        mock_items.return_value = b"<rss/>"
        mock_get_feed.return_value = []

        limits = {url: {"max_articles": None, "lookback_days": 1}}
        global_update = datetime.now() - timedelta(days=3)
        with patch("rss_email.retrieve_articles.get_feed_limits", return_value=limits):
            retrieve_rss_feeds("feed.json", global_update)

        call_update_date = mock_get_feed.call_args[0][2]
        # Feed-specific date should be ~1 day ago, not 3 days ago
        expected_floor = datetime.now() - timedelta(days=2)
        self.assertGreater(call_update_date, expected_floor)


if __name__ == "__main__":
    unittest.main()
