"""Tests for the retrieve_articles module."""
import os
from unittest.mock import MagicMock, patch


import boto3
from moto import mock_s3

import rss_email.retrieve_articles
from rss_email.retrieve_articles import (create_rss, get_feed_urls)


EXAMPLE_RSS_FILE = '''
{
        "feeds": [
            {
                "name": "Test Feed A",
                "url": "https://foo.com/feed/"
            },
            {
                "name": "Test Feed B",
                "url": "https://bar.com/posts.atom"
            },
            {
                "name": "Test Feed C",
                "_url": "https://acme.com/feed.xml"
            }
        ]
    }
'''

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

@patch('rss_email.retrieve_articles.files')
def test_get_feed_urls(mock_file):
    """Tests that the feed URLs are returned correctly."""
    mock_file_obj = MagicMock()
    mock_file_obj.read_text.return_value = EXAMPLE_RSS_FILE
    mock_file.return_value.joinpath.return_value = mock_file_obj
    feed_urls = get_feed_urls('dummyfile.json')
    assert len(feed_urls) == 2
    assert feed_urls[0] == 'https://foo.com/feed/'
    assert feed_urls[1] == 'https://bar.com/posts.atom'

@mock_s3
def test_get_feed_urls_s3():
    """Tests that the feed URLs are returned correctly from an S3 file."""
    # Set up mock S3 bucket
    bucket_name = 'test-bucket'
    key = 'test-key'
    s3 = boto3.client('s3')
    s3.create_bucket(Bucket=bucket_name)
    s3.put_object(Bucket=bucket_name, Key=key, Body=EXAMPLE_RSS_FILE)

    # Call get_feed_urls function with S3 file path
    feed_file = f"s3://{bucket_name}/{key}"
    feed_urls = get_feed_urls(feed_file)

    # Check that the feed URLs were extracted correctly
    assert len(feed_urls) == 2
    assert feed_urls[0] == 'https://foo.com/feed/'
    assert feed_urls[1] == 'https://bar.com/posts.atom'
