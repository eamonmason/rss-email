"""Unit tests for ProcessedArticle model with comments field."""

import unittest
from rss_email.article_processor import ProcessedArticle, _process_article_entry


class TestProcessedArticleComments(unittest.TestCase):
    """Test cases for ProcessedArticle model with comments field."""

    def test_processed_article_with_comments(self):
        """Test that ProcessedArticle can be created with comments field."""
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
            comments="https://example.com/article/comments"
        )

        self.assertEqual(article.title, "Test Article")
        self.assertEqual(article.comments, "https://example.com/article/comments")

    def test_processed_article_without_comments(self):
        """Test that ProcessedArticle can be created without comments field."""
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT"
        )

        self.assertEqual(article.title, "Test Article")
        self.assertIsNone(article.comments)

    def test_process_article_entry_with_comments(self):
        """Test that _process_article_entry preserves comments from source articles."""
        # Create sample article data from Claude response
        claude_article = {
            "id": "article_0",
            "title": "Test Article",
            "link": "https://example.com/article",
            "summary": "This is a test summary",
            "pubdate": "Mon, 26 May 2025 08:00:00 GMT",
            "related_articles": []
        }

        # Create sample source articles with comments
        source_articles = [
            {
                "title": "Test Article",
                "description": "Original description",
                "comments": "https://example.com/article/comments"
            }
        ]

        # Process the article
        processed = _process_article_entry(
            claude_article,
            source_articles,
            "Technology"
        )

        self.assertEqual(processed.title, "Test Article")
        self.assertEqual(processed.comments, "https://example.com/article/comments")
        self.assertEqual(processed.original_description, "Original description")

    def test_process_article_entry_without_comments(self):
        """Test that _process_article_entry handles missing comments gracefully."""
        # Create sample article data from Claude response
        claude_article = {
            "id": "article_0",
            "title": "Test Article",
            "link": "https://example.com/article",
            "summary": "This is a test summary",
            "pubdate": "Mon, 26 May 2025 08:00:00 GMT",
            "related_articles": []
        }

        # Create sample source articles without comments
        source_articles = [
            {
                "title": "Test Article",
                "description": "Original description"
            }
        ]

        # Process the article
        processed = _process_article_entry(
            claude_article,
            source_articles,
            "Technology"
        )

        self.assertEqual(processed.title, "Test Article")
        self.assertIsNone(processed.comments)
        self.assertEqual(processed.original_description, "Original description")


if __name__ == "__main__":
    unittest.main()
