#!/usr/bin/env python3
"""Lambda function to convert an RSS XML file in S3 to an email, and send it."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from importlib.resources import files
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from xml.etree import ElementTree

import boto3
import pydantic
from botocore.exceptions import ClientError
from bs4 import BeautifulSoup

try:
    from .article_processor import (
        ClaudeRateLimiter,
        group_articles_by_priority,
        process_articles_with_claude,
    )

    CLAUDE_AVAILABLE = True
except ImportError:
    # For local testing or when article_processor is not available
    CLAUDE_AVAILABLE = False
    ClaudeRateLimiter = None
    group_articles_by_priority = None
    process_articles_with_claude = None

CHARSET = "UTF-8"
DAYS_OF_NEWS = 3
EMAIL_SUBJECT = "Daily News"
DESCRIPTION_MAX_LENGTH = 1000

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_description_body(html: Optional[str]) -> str:
    """Return the body of the description, without any iframes."""
    if html is None:
        return ""
    parsed_html = BeautifulSoup(html, features="html.parser")
    for s in parsed_html.select("iframe"):
        s.decompose()

    body_text = str("")
    if parsed_html.find("html"):
        if parsed_html.body:
            body_text = str(parsed_html.body)
        else:
            body_text = str(parsed_html.html)
    else:
        body_text = str(parsed_html)

    if len(body_text) == 0:
        body_text = parsed_html.get_text()
    if len(body_text) > DESCRIPTION_MAX_LENGTH:
        body_text = parsed_html.get_text()[:DESCRIPTION_MAX_LENGTH] + "..."

    return body_text


def get_last_run(parameter_name: str) -> datetime:
    """Get the last run timestamp from parameter store."""
    try:
        ssm = boto3.client("ssm")
        parameter = ssm.get_parameter(Name=parameter_name)
        return datetime.strptime(
            parameter["Parameter"]["Value"], "%Y-%m-%dT%H:%M:%S.%f"
        )
    except ClientError as e:
        logger.warning(e)
        logger.warning(
            "Error retrieving parameter from parameter store, retrieving default days."
        )
        return datetime.today() - timedelta(days=DAYS_OF_NEWS)


@pydantic.validate_call(validate_return=True)
def set_last_run(parameter_name: str) -> None:
    """Set the last run timestamp in parameter store."""
    current_timestamp = datetime.now().isoformat()
    ssm = boto3.client("ssm")
    ssm.put_parameter(
        Name=parameter_name,
        Value=current_timestamp,
        Type="String",
        Overwrite=True,
        Description="The last run timestamp of the RSS email I send out",
    )


def add_attribute_to_dict(
    item: ElementTree.Element, name: str, target_dict: Dict[str, str]
) -> None:
    """Add an attribute to a dictionary."""
    tmp_attribute = item.find(name)
    if tmp_attribute is not None:
        if name == "description":
            target_dict[name] = get_description_body(tmp_attribute.text)
        else:
            if tmp_attribute.text is not None:
                target_dict[name] = tmp_attribute.text


@pydantic.validate_call(validate_return=True)
def read_s3_file(bucket_name: str, s3_key: str) -> str:
    """Read a file from S3."""
    s3 = boto3.client("s3")
    s3_response = s3.get_object(Bucket=bucket_name, Key=s3_key)
    file_content = s3_response.get("Body").read().decode("utf-8")
    return file_content


@pydantic.validate_call(validate_return=True)
def get_feed_file(
    s3_bucket: str, s3_prefix: str, local_file: Optional[str] = None
) -> str:
    """Get the feed file."""
    rss_file = None
    if local_file:
        with open(local_file, "r", encoding="UTF-8") as file:
            rss_file = file.read()
    else:
        try:
            rss_file = read_s3_file(s3_bucket, s3_prefix)
        except HTTPError:
            logger.error("Error retrieving RSS file: %s/%s", s3_bucket, s3_prefix)
            return "Internal error retrieving RSS file."
    return rss_file


@pydantic.validate_call(validate_return=True)
def filter_items(rss_file: str, last_run_date: datetime):
    """Filter items based on the last run date."""
    all_items = []
    logger.debug("Retrieved RSS file. Last run date: %s", last_run_date)
    for item in ElementTree.fromstring(rss_file).findall(".//item"):
        item_dict = {}
        for name in ["title", "link", "description", "pubDate"]:
            add_attribute_to_dict(item, name, item_dict)

        published_date = time.mktime(
            datetime.strptime(
                str(item_dict["pubDate"]), "%a, %d %b %Y %H:%M:%S %Z"
            ).timetuple()
        )
        item_dict["sortDate"] = published_date
        if datetime.fromtimestamp(published_date) > last_run_date:
            all_items.append(item_dict)
    return all_items


def generate_enhanced_html_content(
    categorized_articles, article_map: Dict[str, Dict[str, Any]]
) -> str:
    """Generate the categorized HTML content for the enhanced email template."""
    content_parts = []
    article_counter = 0

    for category, articles in categorized_articles:
        # Category header
        category_color = "#667eea"
        if "technology" in category.lower():
            category_color = "#2196F3"
        elif "ai" in category.lower() or "ml" in category.lower():
            category_color = "#9C27B0"
        elif "cybersecurity" in category.lower():
            category_color = "#F44336"
        elif "programming" in category.lower():
            category_color = "#4CAF50"
        elif "science" in category.lower():
            category_color = "#FF9800"

        content_parts.append(f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom: 30px;">
            <tr>
                <td>
                    <table width="100%" cellpadding="12" cellspacing="0" border="0" style="background-color: {category_color}; border-radius: 6px; margin-bottom: 15px;">
                        <tr>
                            <td>
                                <h2 style="color: #ffffff; margin: 0; font-size: 18px; font-weight: bold;">{category}</h2>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        """)

        for article in articles:
            article_counter += 1

            # Build related articles text
            related_html = ""
            if article.related_articles:
                related_titles = []
                for related_id in article.related_articles:
                    idx = int(related_id.split("_")[1])
                    if idx < len(article_map):
                        related_title = list(article_map.values())[idx].get(
                            "title", "Related Article"
                        )
                        related_titles.append(related_title)

                if related_titles:
                    related_html = f"""
                    <tr>
                        <td style="padding: 8px 15px; background-color: #e9ecef; border-radius: 4px;">
                            <p style="margin: 0; font-size: 12px; color: #666;">
                                <strong>Related:</strong> {", ".join(related_titles)}
                            </p>
                        </td>
                    </tr>"""

            # Ensure the link is properly formatted with protocol
            article_link = article.link
            if not article_link.startswith(("http://", "https://")):
                article_link = "https://" + article_link

            content_parts.append(f'''
            <tr>
                <td>
                    <table width="100%" cellpadding="15" cellspacing="0" border="0" style="background-color: #f8f9fa; border-left: 4px solid #3498db; margin-bottom: 15px;">
                        <tr>
                            <td>
                                <h3 style="margin: 0 0 8px 0;">
                                    <a href="{article_link}" target="_blank" style="color: #0066cc; text-decoration: underline; font-size: 16px;">{article.title}</a>
                                </h3>
                                <p style="margin: 0 0 10px 0; font-size: 12px; color: #666;">{article.pubdate}</p>
                                <p style="margin: 0 0 10px 0; font-size: 14px; color: #555; line-height: 1.5;">{article.summary}</p>
                            </td>
                        </tr>
                        {related_html}
                    </table>
                </td>
            </tr>''')

        content_parts.append("</table>")

    return "\n".join(content_parts)


def _generate_claude_enhanced_html(
    filtered_items: List[Dict[str, Any]],
) -> Optional[str]:
    """Generate HTML using Claude categorization if available."""
    if not (
        CLAUDE_AVAILABLE
        and os.environ.get("CLAUDE_ENABLED", "true").lower() == "true"
        and ClaudeRateLimiter is not None
        and process_articles_with_claude is not None
        and group_articles_by_priority is not None
    ):
        return None

    try:
        logger.info("Attempting to process articles with Claude")
        rate_limiter = ClaudeRateLimiter()
        categorized_result = process_articles_with_claude(filtered_items, rate_limiter)

        if categorized_result:
            logger.info("Successfully processed articles with Claude")
            # Create article map for lookups
            article_map = {
                f"article_{i}": item for i, item in enumerate(filtered_items)
            }

            # Get ordered categories
            ordered_categories = group_articles_by_priority(categorized_result)

            # Generate enhanced HTML content
            categorized_content = generate_enhanced_html_content(
                ordered_categories, article_map
            )

            # Load enhanced template - use simpler version for better email client compatibility
            try:
                html_template = (
                    files("rss_email")
                    .joinpath("email_body_enhanced_simple.html")
                    .read_text()
                )
            except FileNotFoundError:
                # Fallback to original enhanced template if simple version not found
                html_template = (
                    files("rss_email").joinpath("email_body_enhanced.html").read_text()
                )

            # Format the template
            return html_template.format(
                subject=EMAIL_SUBJECT,
                generation_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_articles=len(filtered_items),
                total_categories=len(ordered_categories),
                categorized_content=categorized_content,
                ai_model=os.environ.get("CLAUDE_MODEL", "claude-3-5-haiku-latest"),
            )

        logger.warning(
            "Claude processing returned no results, falling back to original format"
        )
    except (ImportError, AttributeError, ValueError) as e:
        logger.error("Error during Claude processing: %s", e, exc_info=True)

    return None


@pydantic.validate_call(validate_return=True)
def generate_html(
    last_run_date: datetime,
    s3_bucket: str,
    s3_prefix: str,
    local_file: Optional[str] = None,
) -> str:
    """Generate the HTML for the email with optional Claude categorization."""
    rss_file = get_feed_file(s3_bucket, s3_prefix, local_file)
    filtered_items = filter_items(rss_file, last_run_date)

    # Try Claude processing first
    claude_html = _generate_claude_enhanced_html(filtered_items)
    if claude_html:
        return claude_html

    # Fallback to original HTML generation
    logger.info("Using original HTML generation (Claude not available or disabled)")
    list_output = ""
    previous_day = ""
    for item in sorted(filtered_items, key=lambda k: k["sortDate"], reverse=True):
        day = item["pubDate"][:3]
        if day != previous_day:
            list_output += f"<p><b>{day}</b></p>\n"
            previous_day = day
        # Ensure link has protocol
        item_link = item["link"]
        if not item_link.startswith(("http://", "https://")):
            item_link = "https://" + item_link

        list_output += f"""
            <div class="tooltip">
            <a href="{item_link}" style="color: white; text-decoration: underline;">{item["title"]}</a>
            <span class="tooltiptext">{item["pubDate"]}</span>
            </div>\n
            <section class="longdescription">{item["description"]}</section>\n"""

    html = files("rss_email").joinpath("email_body.html").read_text()
    return html.format(subject=EMAIL_SUBJECT, articles=list_output)


@pydantic.validate_call(validate_return=True)
def is_valid_email(event_dict: Dict[str, Any], valid_emails: List[str]) -> bool:
    """Check if the email address is valid."""
    if (
        "Records" in event_dict
        and len(event_dict["Records"]) > 0
        and "Sns" in event_dict["Records"][0]
    ):
        ses_notification = event_dict["Records"][0]["Sns"]["Message"]
        json_ses = json.loads(ses_notification)
        if json_ses["mail"]["source"].lower() not in [
            email.lower() for email in valid_emails
        ]:
            logger.warning("Invalid email address: %s", json_ses["mail"]["source"])
            return False

    return True


def send_email(event: Dict[str, Any], context: Optional[Any] = None) -> None:  # pylint: disable=W0613
    """Send the email."""
    logger.debug("Event body: %s", event)

    bucket = os.environ["BUCKET"]
    key = os.environ["KEY"]
    source_email_address = os.environ["SOURCE_EMAIL_ADDRESS"]
    to_email_address = os.environ["TO_EMAIL_ADDRESS"]
    parameter_name = os.environ["LAST_RUN_PARAMETER"]
    if not is_valid_email(event, [to_email_address]):
        return
    run_date = get_last_run(parameter_name)

    body = generate_html(run_date, bucket, key)

    # Create a new SES resource
    client = boto3.client("ses")

    # Try to send the email.
    try:
        # Provide the contents of the email.
        response = client.send_email(
            Destination={
                "ToAddresses": [
                    to_email_address,
                ],
            },
            Message={
                "Body": {
                    "Html": {
                        "Charset": CHARSET,
                        "Data": body,
                    },
                    "Text": {
                        "Charset": CHARSET,
                        "Data": body,
                    },
                },
                "Subject": {
                    "Charset": CHARSET,
                    "Data": EMAIL_SUBJECT,
                },
            },
            Source=source_email_address,
        )
    except ClientError as e:
        logger.warning("got an error: %s", e.response)
    else:
        logger.debug("Email sent! Message ID: %s", response["MessageId"])
        set_last_run(parameter_name)


@pydantic.validate_call(validate_return=True)
def main() -> None:
    """Main function when invoked from command line and not lambda."""
    parser = argparse.ArgumentParser(
        description="Creates a HTML version of RSS for email delivery."
    )
    parser.add_argument(
        "rss_host", type=str, help="XML RSS S3 bucket, e.g. myfeedbucket"
    )
    parser.add_argument("rss_prefix", type=str, help="XML RSS file, e.g. rss.xml")
    # Add optional argument to retrieve a file locally instead of from an S3 bucket
    parser.add_argument("--local-file", type=str, help="Path to a local XML RSS file")

    args = parser.parse_args()
    run_date = datetime.today() - timedelta(days=DAYS_OF_NEWS)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)
    logger.info(
        generate_html(run_date, args.rss_host, args.rss_prefix, args.local_file)
    )


if __name__ == "__main__":
    main()
