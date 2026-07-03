"""Comprehensive end-to-end tests for the email articles flow."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from rss_email.email_articles import generate_html
from rss_email.article_processor import process_articles_with_claude, ClaudeRateLimiter


class TestComprehensiveEmailFlow(unittest.TestCase):
    """Comprehensive test cases for the complete email flow."""

    def setUp(self):
        """Set up test fixtures."""
        # Use current dates to ensure articles pass date filtering
        now = datetime.now()
        recent_date1 = now - timedelta(minutes=30)
        recent_date2 = now - timedelta(minutes=45)
        recent_date3 = now - timedelta(minutes=60)

        self.sample_rss = json.dumps([
            {
                "title": "AI Breakthrough: New Machine Learning Model",
                "link": "https://example.com/ai-breakthrough",
                "description": "New ML model shows promising NLP results.",
                "pubDate": recent_date1.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "sortDate": recent_date1.timestamp(),
            },
            {
                "title": "Cybersecurity Alert: New Vulnerability Discovered",
                "link": "https://example.com/security-alert",
                "description": "Critical vulnerability found in web frameworks.",
                "pubDate": recent_date2.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "sortDate": recent_date2.timestamp(),
            },
            {
                "title": "Tech News: Python 3.13 Released",
                "link": "https://example.com/python-release",
                "description": "Python latest version has performance improvements.",
                "pubDate": recent_date3.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "sortDate": recent_date3.timestamp(),
            },
        ])

        # Stage 1: grouping response (each article in its own group)
        self.mock_grouping_response = {
            "groups": [
                ["article_0"],
                ["article_1"],
                ["article_2"],
            ],
            "article_count": 3,
        }

        # Stage 2: summarize+categorize response keyed by group_id
        self.mock_claude_response = {
            "categories": {
                "AI/ML": [
                    {
                        "group_id": "group_0",
                        "title": "AI Breakthrough: New Machine Learning Model",
                        "summary": "Researchers developed a new ML model showing promising NLP results.",
                        "category": "AI/ML",
                    }
                ],
                "Cybersecurity": [
                    {
                        "group_id": "group_1",
                        "title": "Cybersecurity Alert: New Vulnerability Discovered",
                        "summary": "Critical vulnerability found in popular web frameworks.",
                        "category": "Cybersecurity",
                    }
                ],
                "Technology": [
                    {
                        "group_id": "group_2",
                        "title": "Tech News: Python 3.13 Released",
                        "summary": "Latest Python version includes performance improvements.",
                        "category": "Technology",
                    }
                ],
            },
            "group_count": 3,
            "verification": "processed_all_groups",
        }

    @staticmethod
    def _make_two_stage_side_effect(grouping_json, summary_json):
        """Return a side_effect that produces grouping then summary responses."""
        grouping = MagicMock()
        grouping.content = [MagicMock()]
        grouping.content[0].text = json.dumps(grouping_json)
        grouping.usage.input_tokens = 200
        grouping.usage.output_tokens = 100

        summary = MagicMock()
        summary.content = [MagicMock()]
        summary.content[0].text = json.dumps(summary_json)
        summary.usage.input_tokens = 1000
        summary.usage.output_tokens = 500

        return [grouping, summary]

    def test_generate_html_with_local_file(self):
        """Test HTML generation with local file instead of S3."""
        # Create a temporary RSS file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(self.sample_rss)
            temp_file = f.name

        try:
            # Test with Claude disabled
            with patch.dict("os.environ", {"CLAUDE_ENABLED": "false"}):
                last_run_date = datetime.now() - timedelta(hours=2)
                html = generate_html(last_run_date, "dummy-bucket", "dummy-key", temp_file)

                # Verify HTML contains articles
                self.assertIn("AI Breakthrough", html)
                self.assertIn("Cybersecurity Alert", html)
                self.assertIn("Python 3.13", html)
                self.assertIn("Daily News", html)
        finally:
            os.unlink(temp_file)

    def test_claude_rate_limiter(self):
        """Test the Claude rate limiter functionality."""
        rate_limiter = ClaudeRateLimiter()

        # Test initial state
        self.assertTrue(rate_limiter.can_make_request(1000))
        self.assertEqual(rate_limiter.current_requests, 0)
        self.assertEqual(rate_limiter.current_tokens, 0)

        # Test recording usage
        rate_limiter.record_usage(1000)
        self.assertEqual(rate_limiter.current_requests, 1)
        self.assertEqual(rate_limiter.current_tokens, 1000)

        # Test usage stats
        stats = rate_limiter.get_usage_stats()
        self.assertEqual(stats["requests_made"], 1)
        self.assertEqual(stats["tokens_used"], 1000)
        self.assertGreater(stats["requests_remaining"], 0)
        self.assertGreater(stats["tokens_remaining"], 0)

    @patch("rss_email.article_grouper.anthropic.Anthropic")
    @patch("rss_email.article_processor.anthropic.Anthropic")
    def test_claude_processing_with_real_data(self, mock_proc_anthropic, mock_grp_anthropic):
        """Test Claude processing with actual RSS data structure."""
        articles = [
            {
                "title": "AI Breakthrough: New Machine Learning Model",
                "link": "https://example.com/ai-breakthrough",
                "description": "New ML model shows promising NLP results.",
                "pubDate": "Thu, 02 Jan 2025 12:00:00 GMT",
                "sortDate": 1735819200,
            },
            {
                "title": "Cybersecurity Alert: New Vulnerability Discovered",
                "link": "https://example.com/security-alert",
                "description": "Critical vulnerability found in web frameworks.",
                "pubDate": "Thu, 02 Jan 2025 10:30:00 GMT",
                "sortDate": 1735813800,
            },
        ]

        # Same client instance used for both stages
        mock_client_instance = MagicMock()
        mock_proc_anthropic.return_value = mock_client_instance
        mock_grp_anthropic.return_value = mock_client_instance

        # Build a 2-article grouping response and matching summary response
        grouping_response = {
            "groups": [["article_0"], ["article_1"]],
            "article_count": 2,
        }
        summary_response = {
            "categories": {
                "AI/ML": [self.mock_claude_response["categories"]["AI/ML"][0]],
                "Cybersecurity": [
                    self.mock_claude_response["categories"]["Cybersecurity"][0]
                ],
            },
            "group_count": 2,
        }
        mock_client_instance.messages.create.side_effect = (
            self._make_two_stage_side_effect(grouping_response, summary_response)
        )

        with patch.dict("os.environ", {
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key",
        }):
            rate_limiter = ClaudeRateLimiter()
            result = process_articles_with_claude(articles, rate_limiter)

            self.assertIsNotNone(result)
            self.assertIn("AI/ML", result.categories)
            self.assertIn("Cybersecurity", result.categories)
            self.assertEqual(len(result.categories["AI/ML"]), 1)
            self.assertEqual(len(result.categories["Cybersecurity"]), 1)

            # Two Claude calls (grouping + summarize) → 2 requests, 1800 total tokens
            self.assertEqual(rate_limiter.current_requests, 2)
            self.assertEqual(rate_limiter.current_tokens, 1800)

    @patch("rss_email.article_grouper.anthropic.Anthropic")
    @patch("rss_email.article_processor.anthropic.Anthropic")
    def test_claude_processing_with_json_repair(self, mock_proc_anthropic, mock_grp_anthropic):
        """Even with malformed grouping output, summarize stage still succeeds."""
        articles = [
            {
                "title": "Test Article",
                "link": "https://example.com/test",
                "description": "Test description",
                "pubDate": "Thu, 02 Jan 2025 12:00:00 GMT",
                "sortDate": 1735819200,
            }
        ]

        mock_client_instance = MagicMock()
        mock_proc_anthropic.return_value = mock_client_instance
        mock_grp_anthropic.return_value = mock_client_instance

        # First call: malformed grouping → falls back to singleton
        bad_grouping = MagicMock()
        bad_grouping.content = [MagicMock()]
        bad_grouping.content[0].text = "{garbage"
        bad_grouping.usage.input_tokens = 100
        bad_grouping.usage.output_tokens = 50

        # Second call: well-formed summary response
        summary = MagicMock()
        summary.content = [MagicMock()]
        summary.content[0].text = json.dumps({
            "categories": {
                "Technology": [
                    {
                        "group_id": "group_0",
                        "title": "Test Article",
                        "summary": "Test summary.",
                        "category": "Technology",
                    }
                ]
            },
            "group_count": 1,
        })
        summary.usage.input_tokens = 500
        summary.usage.output_tokens = 300

        mock_client_instance.messages.create.side_effect = [bad_grouping, summary]

        with patch.dict("os.environ", {
            "CLAUDE_ENABLED": "true",
            "CLAUDE_MODEL": "claude-haiku-4-5-20251001",
            "ANTHROPIC_API_KEY": "test-key",
        }):
            rate_limiter = ClaudeRateLimiter()
            result = process_articles_with_claude(articles, rate_limiter)

            self.assertIsNotNone(result)
            self.assertIn("Technology", result.categories)
            self.assertEqual(len(result.categories["Technology"]), 1)
            self.assertEqual(result.categories["Technology"][0].title, "Test Article")


if __name__ == "__main__":
    unittest.main()
