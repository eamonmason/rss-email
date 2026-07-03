"""Unit tests for the get_last_run/set_last_run SSM parameter helpers."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from rss_email.email_articles import get_last_run


class TestGetLastRun(unittest.TestCase):
    """get_last_run must tolerate whatever set_last_run wrote."""

    @patch("rss_email.email_articles.boto3.client")
    def test_parses_value_without_microseconds(self, mock_boto3_client):
        """set_last_run's isoformat() omits '.%f' when microsecond == 0.

        get_last_run previously parsed with a fixed "%Y-%m-%dT%H:%M:%S.%f"
        format, so a value like "2026-01-01T07:30:00" raised ValueError,
        which was uncaught and crashed every subsequent run.
        """
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "2026-01-01T07:30:00"}
        }
        mock_boto3_client.return_value = mock_ssm

        result = get_last_run("test-parameter")

        self.assertEqual(result, datetime(2026, 1, 1, 7, 30, 0))

    @patch("rss_email.email_articles.boto3.client")
    def test_parses_value_with_microseconds(self, mock_boto3_client):
        """Values with microseconds (the common case) must still parse."""
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": "2026-01-01T07:30:00.123456"}
        }
        mock_boto3_client.return_value = mock_ssm

        result = get_last_run("test-parameter")

        self.assertEqual(result, datetime(2026, 1, 1, 7, 30, 0, 123456))


if __name__ == "__main__":
    unittest.main()
