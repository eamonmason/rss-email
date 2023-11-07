"""Tests for the retrieve_articles module."""
import os
from unittest.mock import MagicMock

import boto3
from moto import mock_s3

import rss_email.retrieve_articles
from rss_email.retrieve_articles import (create_rss, get_feed_urls)


@mock_s3
def test_create_rss():
    """Tests that the RSS file is created and uploaded to S3."""

    # Set up mock S3 bucket
    bucket_name = 'test-bucket'
    key = 'test-key'
    content='test'
    s3 = boto3.client('s3')
    s3.create_bucket(Bucket=bucket_name)
    rss_email.retrieve_articles.retrieve_rss_feeds = MagicMock(return_value=content)

    # Call create_rss function, with appropriate env variables
    os.environ['BUCKET'] = bucket_name
    os.environ['KEY'] = key

    create_rss(None, None)

    # Check that the file was uploaded to S3
    obj = s3.get_object(Bucket=bucket_name, Key=key)
    print(obj)
    assert obj['Body'].read().decode('ASCII') == content

def test_get_feed_urls():
    """Tests that the feed URLs are returned correctly."""
    feed_urls = get_feed_urls(os.path.join(os.path.dirname(__file__), 'test_urls.json'))
    assert len(feed_urls) == 2
    assert feed_urls[0] == 'https://foo.com/feed/'
    assert feed_urls[1] == 'https://bar.com/posts.atom'
