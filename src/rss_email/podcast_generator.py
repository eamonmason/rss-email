"""S3, CloudFront and RSS feed helpers for publishing the podcast.

Script writing lives in ``podcast_script.py`` and speech synthesis in
``podcast_tts.py``; this module only publishes the finished episode.
"""

import logging
from datetime import datetime
from typing import List, Optional
from xml.dom import minidom
from xml.etree import ElementTree
from xml.etree.ElementTree import Element, SubElement, tostring

import boto3
import pydantic
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@pydantic.validate_call(validate_return=True)
def upload_to_s3(bucket: str, key: str, data: bytes, content_type: str) -> bool:
    """Upload data to S3."""
    s3 = boto3.client("s3")
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type
        )
        return True
    except ClientError as e:
        logger.error("Error uploading to S3: %s", e)
        return False


@pydantic.validate_call(validate_return=True)
def get_cloudfront_domain(parameter_name: str) -> Optional[str]:
    """
    Retrieve CloudFront domain from Parameter Store.

    Args:
        parameter_name: SSM parameter name containing CloudFront domain

    Returns:
        CloudFront domain name or None if not found
    """
    ssm = boto3.client("ssm")
    try:
        response = ssm.get_parameter(Name=parameter_name)
        domain = response['Parameter']['Value']
        logger.info("Retrieved CloudFront domain from parameter '%s': %s", parameter_name, domain)
        return domain
    except ClientError as e:
        logger.warning(
            "Failed to retrieve parameter '%s' from parameter store: %s",
            parameter_name,
            e
        )
        return None


@pydantic.validate_call(validate_return=True)
def invalidate_cloudfront_cache(distribution_id: str, paths: List[str]) -> bool:
    """
    Invalidate CloudFront cache for specified paths.

    Args:
        distribution_id: CloudFront distribution ID
        paths: List of paths to invalidate (e.g., ['/podcasts/feed.xml'])

    Returns:
        True if invalidation was successful, False otherwise
    """
    cloudfront = boto3.client("cloudfront")
    try:
        response = cloudfront.create_invalidation(
            DistributionId=distribution_id,
            InvalidationBatch={
                'Paths': {
                    'Quantity': len(paths),
                    'Items': paths
                },
                'CallerReference': str(datetime.now().timestamp())
            }
        )
        invalidation_id = response['Invalidation']['Id']
        logger.info(
            "Created CloudFront invalidation %s for distribution %s, paths: %s",
            invalidation_id,
            distribution_id,
            paths
        )
        return True
    except ClientError as e:
        logger.error(
            "Failed to create CloudFront invalidation for distribution %s: %s",
            distribution_id,
            e
        )
        return False


@pydantic.validate_call(validate_return=True)
def update_podcast_feed(
    bucket: str,
    audio_url: str,
    title: str,
    description: str,
    pub_date: str,
    *,
    audio_size: int,
    cloudfront_domain: Optional[str] = None,
    distribution_id: Optional[str] = None
) -> bool:
    """
    Update or create the podcast RSS feed with a new episode.

    Args:
        bucket: S3 bucket name
        audio_url: Public URL to the podcast audio file
        title: Episode title
        description: Episode description
        pub_date: Publication date in ISO format
        audio_size: Size of audio file in bytes
        cloudfront_domain: Optional CloudFront domain to use for feed link
        distribution_id: Optional CloudFront distribution ID for cache invalidation

    Returns:
        True if successful, False otherwise
    """
    s3 = boto3.client("s3")
    feed_key = "podcasts/feed.xml"

    # Try to read existing feed
    try:
        response = s3.get_object(Bucket=bucket, Key=feed_key)
        feed_content = response['Body'].read().decode('utf-8')
        # Parse existing feed to extract items
        root = ElementTree.fromstring(feed_content)
        channel = root.find('channel')
        existing_items = list(channel.findall('item')) if channel is not None else []
    except s3.exceptions.NoSuchKey:
        logger.info("No existing feed found, creating new one")
        existing_items = []
    except (ClientError, ElementTree.ParseError, UnicodeDecodeError) as e:
        logger.warning("Error reading existing feed, creating new one: %s", e)
        existing_items = []

    # Create new RSS feed
    rss = Element('rss', version='2.0')
    rss.set('xmlns:itunes', 'http://www.itunes.com/dtds/podcast-1.0.dtd')
    rss.set('xmlns:atom', 'http://www.w3.org/2005/Atom')

    channel = SubElement(rss, 'channel')

    # Channel metadata
    SubElement(channel, 'title').text = "Eamon's RSS Brief"

    # Use CloudFront domain if available, otherwise fall back to S3
    feed_link = (
        f"https://{cloudfront_domain}/podcasts/feed.xml"
        if cloudfront_domain
        else f"https://{bucket}.s3.amazonaws.com/podcasts/feed.xml"
    )
    SubElement(channel, 'link').text = feed_link

    SubElement(channel, 'description').text = (
        "A single-narrator audio edition of the daily RSS Brief: the stories that matter in technology, AI and security"
    )
    SubElement(channel, 'language').text = 'en-us'

    # iTunes-specific tags
    SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}author').text = (
        'Eamon Mason'
    )
    SubElement(channel, '{http://www.itunes.com/dtds/podcast-1.0.dtd}explicit').text = 'no'

    # Add new episode as first item
    item = SubElement(channel, 'item')
    SubElement(item, 'title').text = title
    SubElement(item, 'description').text = description
    SubElement(item, 'pubDate').text = (
        datetime.fromisoformat(pub_date).strftime('%a, %d %b %Y %H:%M:%S GMT')
    )
    SubElement(item, 'guid').text = audio_url

    # Enclosure (audio file)
    enclosure = SubElement(item, 'enclosure')
    enclosure.set('url', audio_url)
    enclosure.set('length', str(audio_size))
    enclosure.set('type', 'audio/mpeg')

    # Add existing items (up to 10 most recent)
    for existing_item in existing_items[:9]:
        channel.append(existing_item)

    # Generate XML
    xml_str = tostring(rss, encoding='unicode')
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent='  ')

    # Remove extra blank lines
    pretty_xml = '\n'.join([line for line in pretty_xml.split('\n') if line.strip()])

    # Upload to S3
    try:
        s3.put_object(
            Bucket=bucket,
            Key=feed_key,
            Body=pretty_xml.encode('utf-8'),
            ContentType='application/rss+xml'
        )
        logger.info("Podcast feed updated successfully at s3://%s/%s", bucket, feed_key)

        # Invalidate CloudFront cache if distribution ID is provided
        if distribution_id:
            if invalidate_cloudfront_cache(distribution_id, ['/podcasts/feed.xml']):
                logger.info("CloudFront cache invalidated successfully")
            else:
                logger.warning("Failed to invalidate CloudFront cache, feed may be stale")

        return True
    except ClientError as e:
        logger.error("Error uploading podcast feed: %s", e)
        return False
