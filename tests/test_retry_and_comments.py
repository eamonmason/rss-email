import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

from rss_email.retrieve_articles import get_feed_items, get_feed

class TestRetryAndComments(unittest.TestCase):
    @patch("rss_email.retrieve_articles.time.sleep")
    @patch("urllib.request.urlopen")
    def test_get_feed_items_retry(self, mock_urlopen, mock_sleep):
        """Test that get_feed_items retries on failure."""
        # Mock urlopen to fail twice then succeed
        mock_response = MagicMock()
        mock_response.read.return_value = b"<rss></rss>"
        
        # Create a side effect that raises an exception twice, then returns the mock response
        # We need to handle the context manager protocol for urlopen
        mock_context = MagicMock()
        mock_context.__enter__.return_value = mock_response
        mock_context.__exit__.return_value = None
        
        # Mock to fail always
        mock_urlopen.side_effect = Exception("Always fail")
        
        try:
            get_feed_items("https://example.com", datetime.now())
        except Exception:
            pass
            
        # Should be called 3 times
        self.assertEqual(mock_urlopen.call_count, 3)
        # Verify sleep was called (2 retries = 2 sleeps)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_get_feed_comments(self):
        # Test that comments are extracted
        
        # Mock feedparser
        with patch("rss_email.retrieve_articles.feedparser") as mock_feedparser:
            mock_entry = MagicMock()
            mock_entry.title = "Test Article"
            mock_entry.link = "http://example.com/article"
            mock_entry.published_parsed = datetime.now().timetuple()
            mock_entry.comments = "http://example.com/article/comments"
            
            mock_feed = MagicMock()
            mock_feed.entries = [mock_entry]
            mock_feedparser.parse.return_value = mock_feed
            
            articles = get_feed("http://example.com/feed", b"<rss></rss>", datetime(2000, 1, 1))
            
            self.assertEqual(len(articles), 1)
            self.assertEqual(str(articles[0].comments), "http://example.com/article/comments")

if __name__ == "__main__":
    unittest.main()
