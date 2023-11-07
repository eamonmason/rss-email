import json
import os
import pytest
import boto3
from moto import mock_s3
from rss_email.retrieve_articles import retrieve_rss_feeds, create_rss, get_feed_urls
import rss_email.retrieve_articles
from botocore.exceptions import ClientError
import boto3
from moto import mock_s3
import os
from unittest.mock import MagicMock, patch, Mock

@pytest.fixture
def feed_file():
    return 'feed_urls.json'


@pytest.fixture
def rss_content():
    return b'<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0">\n  <channel>\n    <title>Daily Feed</title>\n    <link>http://www.greatnews.com</link>\n    <description>The news to use...</description>\n    <lastBuildDate>2022-01-01 00:00:00</lastBuildDate>\n    <item>\n      <title>Article 1</title>\n      <link>http://www.article1.com</link>\n      <description>Article 1 description</description>\n      <guid>http://www.article1.com</guid>\n      <pubDate>2022-01-01 00:00:00</pubDate>\n    </item>\n  </channel>\n</rss>'


@mock_s3
def test_create_rss(feed_file):
    """Tests that the RSS file is created and uploaded to S3."""

    # Set up mock S3 bucket
    bucket_name = 'test-bucket'
    key = 'test-key'
    rss_content='test'
    s3 = boto3.client('s3')
    s3.create_bucket(Bucket=bucket_name)    
    rss_email.retrieve_articles.retrieve_rss_feeds = MagicMock(return_value=rss_content)

    # Call create_rss function, with appropriate env variables
    os.environ['BUCKET'] = bucket_name
    os.environ['KEY'] = key
    
    create_rss(None, None)

    # Check that the file was uploaded to S3
    obj = s3.get_object(Bucket=bucket_name, Key=key)
    print(obj)
    assert obj['Body'].read().decode('ASCII') == rss_content

def test_get_feed_urls():
    """Tests that the feed URLs are returned correctly."""
    feed_urls = get_feed_urls(os.path.join(os.path.dirname(__file__), 'test_urls.json'))
    assert len(feed_urls) == 2
    assert feed_urls[0] == 'https://foo.com/feed/'
    assert feed_urls[1] == 'https://bar.com/posts.atom'

# def test_main(feed_file):
#     # Load test data
#     with open(feed_file, 'r') as f:
#         feed_data = json.load(f)

#     # Call main function
#     rss_content = main(feed_file)

#     # Check that the RSS content is valid
#     assert rss_content.startswith(b'<?xml version="1.0" encoding="utf-8"?>')
#     assert b'<rss version="2.0">' in rss_content
#     assert b'<title>Daily Feed</title>' in rss_content
#     assert b'<link>http://www.greatnews.com</link>' in rss_content
#     assert b'<description>The news to use...</description>' in rss_content
#     assert b'<lastBuildDate>' in rss_content
#     assert b'<item>' in rss_content

#     # Check that the RSS content contains expected articles
#     for feed in feed_data['feeds']:
#         if 'url' in feed:
#             assert feed['url'].encode() in rss_content