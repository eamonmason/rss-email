"""Tests for the podcast publishing helpers (podcast_generator.py)."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from rss_email.podcast_generator import (
    invalidate_cloudfront_cache,
    update_podcast_feed,
    upload_to_s3,
)


class NoSuchKey(Exception):
    """Stand-in for ``s3.exceptions.NoSuchKey``."""


def _s3_without_feed():
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = NoSuchKey
    s3.get_object.side_effect = NoSuchKey("missing")
    return s3


@patch("rss_email.podcast_generator.boto3.client")
def test_update_feed_creates_new_feed_with_enclosure(mock_boto3_client):
    s3 = _s3_without_feed()
    mock_boto3_client.return_value = s3

    ok = update_podcast_feed(
        bucket="b", audio_url="https://cdn/ep.mp3", title="RSS Brief — 2026-10-27",
        description="Today: x.", pub_date="2026-10-27T07:45:00", audio_size=1234,
    )

    assert ok
    put = s3.put_object.call_args.kwargs
    assert put["Key"] == "podcasts/feed.xml"
    xml = put["Body"].decode()
    assert 'length="1234"' in xml and 'type="audio/mpeg"' in xml
    assert "Eamon's RSS Brief" in xml


@patch("rss_email.podcast_generator.boto3.client")
def test_update_feed_keeps_ten_most_recent_and_invalidates(mock_boto3_client):
    existing_items = "".join(
        f"<item><title>old{i}</title><guid>g{i}</guid></item>" for i in range(12)
    )
    s3 = MagicMock()
    s3.exceptions.NoSuchKey = NoSuchKey
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: f"<rss><channel>{existing_items}</channel></rss>".encode())
    }
    cloudfront = MagicMock()
    cloudfront.create_invalidation.return_value = {"Invalidation": {"Id": "I1"}}
    mock_boto3_client.side_effect = lambda name: s3 if name == "s3" else cloudfront

    update_podcast_feed(
        bucket="b", audio_url="u", title="t", description="d",
        pub_date="2026-10-27T07:45:00", audio_size=1, distribution_id="DIST",
    )

    xml = s3.put_object.call_args.kwargs["Body"].decode()
    assert xml.count("<item>") == 10
    assert "old8" in xml and "old9" not in xml
    cloudfront.create_invalidation.assert_called_once()


@patch("rss_email.podcast_generator.boto3.client")
def test_upload_and_invalidation_report_client_errors(mock_boto3_client):
    error = ClientError({"Error": {"Code": "X", "Message": "y"}}, "Op")
    client = MagicMock()
    client.put_object.side_effect = error
    client.create_invalidation.side_effect = error
    mock_boto3_client.return_value = client

    assert upload_to_s3("b", "k", b"d", "audio/mpeg") is False
    assert invalidate_cloudfront_cache("DIST", ["/x"]) is False
