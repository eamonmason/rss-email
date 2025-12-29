"""Lambda function to retrieve and aggregate RSS feeds, storing results in S3."""

import logging
import os
import tempfile
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

from .retrieve_articles import get_update_date, retrieve_rss_feeds, DAYS_OF_NEWS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:  # pylint: disable=W0613
    """
    Retrieve RSS feeds and store aggregated results in S3.

    Environment Variables:
        BUCKET: S3 bucket name for storing RSS data
        KEY: S3 key for RSS data file (e.g., 'rss.xml')
        FEED_URLS_BUCKET: S3 bucket containing feed_urls.json
        FEED_URLS_KEY: S3 key for feed_urls.json

    Returns:
        {
            "article_count": int,
            "s3_bucket": str,
            "s3_key": str,
            "timestamp": str (ISO format)
        }
    """
    try:
        # Get configuration from environment
        bucket = os.environ["BUCKET"]
        key = os.environ["KEY"]
        feed_urls_bucket = os.environ.get("FEED_URLS_BUCKET", bucket)
        feed_urls_key = os.environ.get("FEED_URLS_KEY", "feed_urls.json")

        logger.info("Retrieving feed URLs from s3://%s/%s", feed_urls_bucket, feed_urls_key)

        # Load feed URLs from S3
        s3 = boto3.client("s3")
        try:
            response = s3.get_object(Bucket=feed_urls_bucket, Key=feed_urls_key)
            feed_urls_content = response["Body"].read().decode("utf-8")
        except ClientError as e:
            logger.error("Failed to load feed URLs from S3: %s", e)
            raise

        # Create temporary file with feed URLs
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_file:
            temp_file.write(feed_urls_content)
            temp_feeds_path = temp_file.name

        logger.info("Retrieving RSS feeds (last %d days)", DAYS_OF_NEWS)

        # Get update date and retrieve RSS feeds
        update_date = get_update_date(DAYS_OF_NEWS)
        rss_content = retrieve_rss_feeds(temp_feeds_path, update_date)

        # Clean up temp file
        os.unlink(temp_feeds_path)

        # Count articles in RSS feed
        import xml.etree.ElementTree as ET  # pylint: disable=C0415  # noqa: N817
        try:
            root = ET.fromstring(rss_content)
            article_count = len(root.findall(".//item"))
        except ET.ParseError:
            logger.warning("Could not parse RSS to count articles, assuming 0")
            article_count = 0

        logger.info(
            "Retrieved %d articles, uploading to s3://%s/%s", article_count, bucket, key
        )

        # Upload to S3
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=rss_content,
            ContentType="application/rss+xml; charset=utf-8",
            ContentEncoding="utf-8",
        )

        logger.info("Successfully processed and stored RSS feed")

        return {
            "article_count": article_count,
            "s3_bucket": bucket,
            "s3_key": key,
            "timestamp": update_date.isoformat(),
        }

    except Exception as e:
        logger.error("Error retrieving and storing RSS articles: %s", e, exc_info=True)
        raise
