"""Unit tests for batch metadata reconstruction (groups + sources)."""

import json
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from rss_email.article_processor import ProcessedArticle
from rss_email.retrieve_and_send_email import (
    build_processed_articles_from_groups,
    retrieve_batch_metadata,
)


def _articles_with_sources(count: int):
    """Build a list of test articles with feed attribution."""
    return [
        {
            "title": f"Article {i}",
            "link": f"https://example.com/{i}",
            "comments": f"https://example.com/{i}/comments",
            "description": f"Description {i}",
            "pubDate": f"Mon, {10 + i} Jan 2026 08:00:00 GMT",
            "sourceName": f"Feed {i % 3}",
            "sourceUrl": f"https://example.com/feed/{i % 3}",
        }
        for i in range(count)
    ]


class TestBatchMetadataReconstruction(unittest.TestCase):
    """Reconstruction of ProcessedArticle from S3 metadata + Claude response."""

    def test_singleton_group_carries_primary_article_metadata(self):
        """A singleton group rebuilds with comments + a single source."""
        original_articles = _articles_with_sources(2)
        groups = [[0], [1]]
        response = {
            "categories": {
                "Technology": [
                    {
                        "group_id": "group_0",
                        "title": "Article 0",
                        "summary": "AI summary 0",
                    },
                    {
                        "group_id": "group_1",
                        "title": "Article 1",
                        "summary": "AI summary 1",
                    },
                ]
            }
        }

        enriched = build_processed_articles_from_groups(
            response, original_articles, groups
        )

        self.assertIn("Technology", enriched)
        self.assertEqual(len(enriched["Technology"]), 2)

        first = enriched["Technology"][0]
        self.assertIsInstance(first, ProcessedArticle)
        self.assertEqual(first.title, "Article 0")
        self.assertEqual(first.summary, "AI summary 0")
        self.assertEqual(first.comments, "https://example.com/0/comments")
        self.assertEqual(len(first.sources), 1)
        self.assertEqual(first.sources[0].feed_name, "Feed 0")

    def test_multi_article_group_merges_sources(self):
        """A multi-member group exposes one source per original article."""
        original_articles = _articles_with_sources(3)
        groups = [[0, 1, 2]]
        response = {
            "categories": {
                "AI/ML": [
                    {
                        "group_id": "group_0",
                        "title": "Combined Story",
                        "summary": "Three feeds covered this event.",
                    }
                ]
            }
        }

        enriched = build_processed_articles_from_groups(
            response, original_articles, groups
        )

        story = enriched["AI/ML"][0]
        self.assertEqual(story.title, "Combined Story")
        self.assertEqual(len(story.sources), 3)
        feed_names = {source.feed_name for source in story.sources}
        self.assertEqual(feed_names, {"Feed 0", "Feed 1", "Feed 2"})

    def test_invalid_group_id_is_skipped(self):
        """Malformed group_id values do not crash and are ignored."""
        original_articles = _articles_with_sources(1)
        groups = [[0]]
        response = {
            "categories": {
                "Technology": [
                    {"group_id": "not-a-group", "title": "X", "summary": "Y"},
                    {"group_id": "group_5", "title": "Out of range", "summary": "Y"},
                    {"group_id": "group_0", "title": "OK", "summary": "Z"},
                ]
            }
        }

        enriched = build_processed_articles_from_groups(
            response, original_articles, groups
        )

        self.assertEqual(len(enriched["Technology"]), 1)
        self.assertEqual(enriched["Technology"][0].title, "OK")

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_batch_metadata_returns_articles_and_groups(self, mock_boto3):
        """The S3 metadata blob is parsed into the articles + groups tuple."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        metadata = {
            "articles": [{"title": "A1"}],
            "groups": [[0]],
            "submitted_at": "2026-01-10T12:00:00",
        }
        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = json.dumps(metadata).encode("utf-8")
        mock_s3.get_object.return_value = mock_response

        articles, groups = retrieve_batch_metadata(
            "test-bucket", "batch-metadata/batch-123.json"
        )

        mock_s3.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="batch-metadata/batch-123.json"
        )
        self.assertEqual(articles, [{"title": "A1"}])
        self.assertEqual(groups, [[0]])

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_batch_metadata_defaults_groups_to_singletons(self, mock_boto3):
        """Legacy metadata without groups produces one-singleton-per-article."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3
        metadata = {"articles": [{"title": "A1"}, {"title": "A2"}]}
        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = json.dumps(metadata).encode("utf-8")
        mock_s3.get_object.return_value = mock_response

        _, groups = retrieve_batch_metadata("test-bucket", "key")

        self.assertEqual(groups, [[0], [1]])

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_batch_metadata_handles_s3_error(self, mock_boto3):
        """S3 errors return empty lists rather than raising."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3
        mock_s3.get_object.side_effect = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
        )

        articles, groups = retrieve_batch_metadata(
            "test-bucket", "batch-metadata/nonexistent.json"
        )

        self.assertEqual(articles, [])
        self.assertEqual(groups, [])

    @patch("rss_email.retrieve_and_send_email.boto3.client")
    def test_retrieve_batch_metadata_handles_invalid_json(self, mock_boto3):
        """Invalid JSON in S3 returns empty lists rather than raising."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3
        mock_response = {"Body": MagicMock()}
        mock_response["Body"].read.return_value = b"invalid json {"
        mock_s3.get_object.return_value = mock_response

        articles, groups = retrieve_batch_metadata("test-bucket", "key")

        self.assertEqual(articles, [])
        self.assertEqual(groups, [])


if __name__ == "__main__":
    unittest.main()
