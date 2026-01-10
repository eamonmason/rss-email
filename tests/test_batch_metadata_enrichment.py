"""Unit tests for batch metadata enrichment functionality."""

import json
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from rss_email.retrieve_and_send_email import (
    retrieve_original_articles,
    enrich_batch_results_with_metadata,
)
from rss_email.article_processor import ProcessedArticle


class TestBatchMetadataEnrichment(unittest.TestCase):
    """Test cases for batch metadata enrichment."""

    def test_enrich_batch_results_with_comments(self):
        """Test that enrich_batch_results_with_metadata restores comments from original articles."""
        # Create original articles with comments
        original_articles = [
            {
                "title": "Article 1",
                "link": "https://example.com/1",
                "comments": "https://example.com/1/comments",
                "description": "Original description 1",
                "pubDate": "Mon, 10 Jan 2026 08:00:00 GMT",
            },
            {
                "title": "Article 2",
                "link": "https://example.com/2",
                "comments": "https://example.com/2/comments",
                "description": "Original description 2",
                "pubDate": "Mon, 10 Jan 2026 09:00:00 GMT",
            },
        ]

        # Create categorized data from Claude (without comments)
        categorized_data = {
            "Technology": [
                {
                    "id": "article_0",
                    "title": "Article 1",
                    "link": "https://example.com/1",
                    "summary": "AI-generated summary 1",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                },
                {
                    "id": "article_1",
                    "title": "Article 2",
                    "link": "https://example.com/2",
                    "summary": "AI-generated summary 2",
                    "pubdate": "Mon, 10 Jan 2026 09:00:00 GMT",
                },
            ]
        }

        # Enrich with metadata
        enriched_categories = enrich_batch_results_with_metadata(
            categorized_data, original_articles
        )

        # Verify comments are restored
        self.assertIn("Technology", enriched_categories)
        self.assertEqual(len(enriched_categories["Technology"]), 2)

        # Check first article
        article1 = enriched_categories["Technology"][0]
        self.assertIsInstance(article1, ProcessedArticle)
        self.assertEqual(article1.title, "Article 1")
        self.assertEqual(article1.comments, "https://example.com/1/comments")
        self.assertEqual(article1.original_description, "Original description 1")
        self.assertEqual(article1.summary, "AI-generated summary 1")

        # Check second article
        article2 = enriched_categories["Technology"][1]
        self.assertIsInstance(article2, ProcessedArticle)
        self.assertEqual(article2.title, "Article 2")
        self.assertEqual(article2.comments, "https://example.com/2/comments")
        self.assertEqual(article2.original_description, "Original description 2")

    def test_enrich_batch_results_missing_metadata(self):
        """Test graceful handling when original articles list is empty."""
        categorized_data = {
            "Technology": [
                {
                    "id": "article_0",
                    "title": "Article 1",
                    "link": "https://example.com/1",
                    "summary": "Summary",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                }
            ]
        }

        # Empty original articles list
        original_articles = []

        # Should not crash, but comments will be None
        enriched_categories = enrich_batch_results_with_metadata(
            categorized_data, original_articles
        )

        self.assertIn("Technology", enriched_categories)
        article = enriched_categories["Technology"][0]
        self.assertIsNone(article.comments)
        self.assertEqual(article.original_description, "")

    def test_enrich_batch_results_invalid_article_id(self):
        """Test handling of malformed article IDs."""
        original_articles = [
            {
                "title": "Article 1",
                "comments": "https://example.com/1/comments",
                "description": "Description",
            }
        ]

        # Invalid article IDs
        categorized_data = {
            "Technology": [
                {
                    "id": "invalid_id",  # No underscore format
                    "title": "Article with invalid ID",
                    "link": "https://example.com/invalid",
                    "summary": "Summary",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                },
                {
                    "id": "article_abc",  # Non-numeric index
                    "title": "Article with non-numeric ID",
                    "link": "https://example.com/abc",
                    "summary": "Summary",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                },
            ]
        }

        # Should handle gracefully without crashing
        enriched_categories = enrich_batch_results_with_metadata(
            categorized_data, original_articles
        )

        self.assertEqual(len(enriched_categories["Technology"]), 2)
        # Both articles should have None comments due to invalid IDs
        self.assertIsNone(enriched_categories["Technology"][0].comments)
        self.assertIsNone(enriched_categories["Technology"][1].comments)

    def test_enrich_batch_results_out_of_range_index(self):
        """Test handling when article index is out of range."""
        original_articles = [
            {
                "title": "Article 1",
                "comments": "https://example.com/1/comments",
                "description": "Description",
            }
        ]

        categorized_data = {
            "Technology": [
                {
                    "id": "article_5",  # Index 5 is out of range
                    "title": "Article with out-of-range index",
                    "link": "https://example.com/5",
                    "summary": "Summary",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                }
            ]
        }

        enriched_categories = enrich_batch_results_with_metadata(
            categorized_data, original_articles
        )

        article = enriched_categories["Technology"][0]
        self.assertIsNone(article.comments)
        self.assertEqual(article.original_description, "")

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_original_articles_success(self, mock_boto3):
        """Test successful retrieval of original articles from S3."""
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        # Mock S3 response
        metadata = {
            "articles": [
                {
                    "title": "Article 1",
                    "comments": "https://example.com/1/comments",
                }
            ],
            "submitted_at": "2026-01-10T12:00:00",
        }

        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = json.dumps(metadata).encode("utf-8")
        mock_s3.get_object.return_value = mock_response

        # Test retrieval
        articles = retrieve_original_articles(
            "test-bucket", "batch-metadata/batch-123.json"
        )

        # Verify S3 call
        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="batch-metadata/batch-123.json"
        )

        # Verify returned articles
        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["title"], "Article 1")
        self.assertEqual(articles[0]["comments"], "https://example.com/1/comments")

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_original_articles_s3_error(self, mock_boto3):
        """Test handling of S3 errors when retrieving metadata."""
        # Mock S3 client that raises an error
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
        )

        # Should return empty list on error
        articles = retrieve_original_articles(
            "test-bucket", "batch-metadata/nonexistent.json"
        )

        self.assertEqual(articles, [])

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_original_articles_invalid_json(self, mock_boto3):
        """Test handling of invalid JSON in S3 metadata file."""
        # Mock S3 client
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        # Mock S3 response with invalid JSON
        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = b"invalid json {"
        mock_s3.get_object.return_value = mock_response

        # Should return empty list on parse error
        articles = retrieve_original_articles(
            "test-bucket", "batch-metadata/invalid.json"
        )

        self.assertEqual(articles, [])

    def test_enrich_batch_results_multi_batch_scenario(self):
        """Test that metadata enrichment works correctly with multiple batches."""
        # Create 50 original articles with comments (simulating 2 batches of 25)
        original_articles = []
        for i in range(50):
            original_articles.append({
                "title": f"Article {i}",
                "link": f"https://example.com/{i}",
                "comments": f"https://example.com/{i}/comments",
                "description": f"Description {i}",
                "pubDate": f"Mon, {10 + i} Jan 2026 08:00:00 GMT",
            })

        # Create categorized data simulating what Claude returns from 2 batches
        # Batch 0: articles 0-24 with IDs article_0 to article_24
        # Batch 1: articles 25-49 with IDs article_25 to article_49 (now with offset!)
        categorized_data = {
            "Technology": [
                {
                    "id": "article_5",  # Should map to original article 5
                    "title": "Article 5",
                    "link": "https://example.com/5",
                    "summary": "AI-generated summary 5",
                    "pubdate": "Mon, 15 Jan 2026 08:00:00 GMT",
                },
                {
                    "id": "article_30",  # Should map to original article 30 (NOT article 5!)
                    "title": "Article 30",
                    "link": "https://example.com/30",
                    "summary": "AI-generated summary 30",
                    "pubdate": "Mon, 40 Jan 2026 08:00:00 GMT",
                },
            ],
            "AI/ML": [
                {
                    "id": "article_0",  # Should map to original article 0
                    "title": "Article 0",
                    "link": "https://example.com/0",
                    "summary": "AI-generated summary 0",
                    "pubdate": "Mon, 10 Jan 2026 08:00:00 GMT",
                },
                {
                    "id": "article_49",  # Should map to original article 49
                    "title": "Article 49",
                    "link": "https://example.com/49",
                    "summary": "AI-generated summary 49",
                    "pubdate": "Mon, 59 Jan 2026 08:00:00 GMT",
                },
            ],
        }

        # Enrich with metadata
        enriched_categories = enrich_batch_results_with_metadata(
            categorized_data, original_articles
        )

        # Verify Technology category
        self.assertIn("Technology", enriched_categories)
        self.assertEqual(len(enriched_categories["Technology"]), 2)

        # Check article 5 from batch 0
        article5 = enriched_categories["Technology"][0]
        self.assertEqual(article5.title, "Article 5")
        self.assertEqual(article5.comments, "https://example.com/5/comments")
        self.assertEqual(article5.original_description, "Description 5")

        # Check article 30 from batch 1 - THIS IS THE CRITICAL TEST
        article30 = enriched_categories["Technology"][1]
        self.assertEqual(article30.title, "Article 30")
        self.assertEqual(article30.comments, "https://example.com/30/comments")
        self.assertEqual(article30.original_description, "Description 30")

        # Verify AI/ML category
        self.assertIn("AI/ML", enriched_categories)
        self.assertEqual(len(enriched_categories["AI/ML"]), 2)

        # Check article 0
        article0 = enriched_categories["AI/ML"][0]
        self.assertEqual(article0.title, "Article 0")
        self.assertEqual(article0.comments, "https://example.com/0/comments")
        self.assertEqual(article0.original_description, "Description 0")

        # Check article 49 from batch 1
        article49 = enriched_categories["AI/ML"][1]
        self.assertEqual(article49.title, "Article 49")
        self.assertEqual(article49.comments, "https://example.com/49/comments")
        self.assertEqual(article49.original_description, "Description 49")


if __name__ == "__main__":
    unittest.main()
