"""Unit tests for HTML generation with comments field."""

import unittest
from rss_email.article_processor import ProcessedArticle
from rss_email.email_articles import generate_enhanced_html_content


class TestHTMLGenerationWithComments(unittest.TestCase):
    """Test cases for HTML generation with comments field."""

    def test_html_generation_with_comments(self):
        """Test that HTML is generated correctly when article has comments."""
        # Create a processed article with comments
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
            comments="https://example.com/article/comments"
        )

        # Create categorized articles structure
        categorized_articles = [("Technology", [article])]
        article_map = {"article_0": {"title": "Test Article"}}

        # Generate HTML
        html = generate_enhanced_html_content(categorized_articles, article_map)

        # Verify that HTML contains the comments link
        self.assertIn("https://example.com/article/comments", html)
        self.assertIn("Comments</a>", html)

    def test_html_generation_without_comments(self):
        """Test that HTML is generated correctly when article has no comments."""
        # Create a processed article without comments
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT"
        )

        # Create categorized articles structure
        categorized_articles = [("Technology", [article])]
        article_map = {"article_0": {"title": "Test Article"}}

        # Generate HTML
        html = generate_enhanced_html_content(categorized_articles, article_map)

        # Verify that HTML doesn't contain comments link
        self.assertNotIn("Comments</a>", html)
        # But still contains the article title
        self.assertIn("Test Article", html)

    def test_html_generation_with_none_comments(self):
        """Test that HTML is generated correctly when article has None comments."""
        # Create a processed article with explicit None comments
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
            comments=None
        )

        # Create categorized articles structure
        categorized_articles = [("Technology", [article])]
        article_map = {"article_0": {"title": "Test Article"}}

        # Generate HTML
        html = generate_enhanced_html_content(categorized_articles, article_map)

        # Verify that HTML doesn't contain comments link
        self.assertNotIn("Comments</a>", html)
        # But still contains the article title
        self.assertIn("Test Article", html)

    def test_html_generation_with_empty_string_comments(self):
        """Test that HTML is generated correctly when article has empty string comments."""
        # Create a processed article with empty string comments
        article = ProcessedArticle(
            title="Test Article",
            link="https://example.com/article",
            summary="This is a test summary",
            category="Technology",
            pubdate="Mon, 26 May 2025 08:00:00 GMT",
            comments=""
        )

        # Create categorized articles structure
        categorized_articles = [("Technology", [article])]
        article_map = {"article_0": {"title": "Test Article"}}

        # Generate HTML
        html = generate_enhanced_html_content(categorized_articles, article_map)

        # Verify that HTML doesn't contain comments link (empty string is falsy)
        self.assertNotIn("Comments</a>", html)
        # But still contains the article title
        self.assertIn("Test Article", html)


if __name__ == "__main__":
    unittest.main()
