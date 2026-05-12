"""Unit tests for ProcessedArticle comments field and source-derived comments."""

import unittest

from rss_email.article_processor import (
    ProcessedArticle,
    _processed_article_from_response,
)


class TestProcessedArticleComments(unittest.TestCase):
    """ProcessedArticle's comments field flows through the new group-driven flow."""

    def test_processed_article_with_comments(self):
        """ProcessedArticle accepts comments directly."""
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
            comments="https://example.com/article/comments",
        )

        self.assertEqual(article.title, "Test Article")
        self.assertEqual(article.comments, "https://example.com/article/comments")

    def test_processed_article_without_comments(self):
        """ProcessedArticle omits comments when not provided."""
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
        )

        self.assertEqual(article.title, "Test Article")
        self.assertIsNone(article.comments)

    def test_group_response_preserves_primary_comments(self):
        """_processed_article_from_response carries the primary article's comments."""
        entry = {
            "group_id": "group_0",
            "title": "Test Article",
            "summary": "This is a test summary",
        }
        source_articles = [
            {
                "title": "Test Article",
                "link": "https://example.com/article",
                "description": "Original description",
                "pubDate": "Mon, 26 May 2025 08:00:00 GMT",
                "comments": "https://example.com/article/comments",
            }
        ]

        processed = _processed_article_from_response(
            entry, [0], source_articles, "Technology"
        )

        self.assertEqual(processed.title, "Test Article")
        self.assertEqual(processed.comments, "https://example.com/article/comments")
        self.assertEqual(processed.original_description, "Original description")
        self.assertEqual(len(processed.sources), 1)
        self.assertEqual(
            processed.sources[0].comments, "https://example.com/article/comments"
        )

    def test_group_response_without_comments(self):
        """_processed_article_from_response handles missing comments."""
        entry = {
            "group_id": "group_0",
            "title": "Test Article",
            "summary": "This is a test summary",
        }
        source_articles = [
            {
                "title": "Test Article",
                "link": "https://example.com/article",
                "description": "Original description",
                "pubDate": "Mon, 26 May 2025 08:00:00 GMT",
            }
        ]

        processed = _processed_article_from_response(
            entry, [0], source_articles, "Technology"
        )

        self.assertEqual(processed.title, "Test Article")
        self.assertIsNone(processed.comments)


if __name__ == "__main__":
    unittest.main()
