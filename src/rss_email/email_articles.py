#!/usr/bin/env python3
"""Lambda function to convert stored articles JSON in S3 to an email, and send it."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys

from datetime import datetime, timedelta
from importlib.resources import files
from typing import Any, Dict, List, Optional

import boto3
import pydantic
from botocore.exceptions import ClientError
from bs4 import BeautifulSoup

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    from .models import DEFAULT_CLAUDE_MODEL
except ImportError:
    # For local testing or when models module is not available
    DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

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
DESCRIPTION_MAX_LENGTH = 400

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@pydantic.validate_call(validate_return=True)
def get_description_body(html_content: Optional[str]) -> str:
    """Return the body of the description, without any iframes."""
    if html_content is None:
        return ""
    parsed_html = BeautifulSoup(html_content, features="html.parser")
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


@pydantic.validate_call(validate_return=True)
def get_last_run(parameter_name: str) -> datetime:
    """Get the last run timestamp from parameter store."""
    try:
        ssm = boto3.client("ssm")
        parameter = ssm.get_parameter(Name=parameter_name)
        return datetime.fromisoformat(parameter["Parameter"]["Value"])
    except (ClientError, ValueError) as e:
        logger.warning(
            "Error retrieving parameter '%s' from parameter store: %s. Using default days.",
            parameter_name,
            e
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
        except ClientError:
            logger.error("Error retrieving RSS file: %s/%s", s3_bucket, s3_prefix)
            raise
    return rss_file


@pydantic.validate_call(validate_return=True)
def filter_items(articles_json: str, last_run_date: datetime):
    """Filter stored articles based on the last run date.

    Storage is a JSON array of RSSItem-shaped dicts (title/link/description/
    pubDate/sortDate/comments/sourceName/sourceUrl). sortDate is the epoch the
    writer already computed at fetch time, so filtering never has to re-parse
    a date string (the source of several date-handling bugs in the old
    RSS-XML round trip).
    """
    try:
        items = json.loads(articles_json)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse stored articles JSON: %s", e)
        return []

    all_items = []
    logger.debug("Retrieved %d stored articles. Last run date: %s", len(items), last_run_date)
    for item_dict in items:
        sort_date = item_dict.get("sortDate")
        if sort_date is None:
            logger.debug(
                "Skipping item without sortDate: %s", item_dict.get("title", "Unknown")
            )
            continue

        pubdate_dt = datetime.fromtimestamp(sort_date)
        logger.debug(
            "Comparing article date %s with last_run_date %s", pubdate_dt, last_run_date
        )
        if pubdate_dt <= last_run_date:
            logger.debug(
                "Skipped article (too old): %s", item_dict.get("title", "Unknown")
            )
            continue

        item_dict["description"] = get_description_body(item_dict.get("description"))
        all_items.append(item_dict)
        logger.debug("Added article: %s", item_dict.get("title", "Unknown"))

    logger.debug("Total filtered items: %d", len(all_items))
    return all_items


def _render_sources_html(sources: List[Any]) -> str:
    """Render the per-article sources line (Source / Also covered by)."""
    if not sources:
        return ""

    def _source_link(source: Any) -> str:
        link = source.get("link") if isinstance(source, dict) else getattr(source, "link", "")
        name = (
            source.get("feed_name") if isinstance(source, dict)
            else getattr(source, "feed_name", None)
        )
        if not link:
            return ""
        if not link.startswith(("http://", "https://")):
            link = "https://" + link
        label = html.escape(name or link)
        return f'<a href="{html.escape(link)}" style="color: #0066cc;">{label}</a>'

    links = [link_html for link_html in (_source_link(s) for s in sources) if link_html]
    if not links:
        return ""

    if len(links) == 1:
        prefix = "Source"
        body = links[0]
    else:
        prefix = "Also covered by"
        body = " · ".join(links)

    return (
        '<p style="margin: 6px 0 0 0; font-size: 0.8em; color: #666; line-height: 1.5;">'
        f'<strong>{prefix}:</strong> {body}'
        '</p>'
    )


@pydantic.validate_call(validate_return=True)
def category_color(category: str) -> str:
    """Return the header background colour hex for a given category name."""
    name = category.lower()
    if "technology" in name:
        return "#2196F3"
    if "ai" in name or "ml" in name:
        return "#9C27B0"
    if "cybersecurity" in name:
        return "#F44336"
    if "programming" in name:
        return "#4CAF50"
    if "science" in name:
        return "#FF9800"
    return "#667eea"


def generate_enhanced_html_content(
    categorized_articles,
    article_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Generate the categorized HTML content for the enhanced email template."""
    _ = article_map  # legacy parameter retained for backward compatibility
    content_parts = []
    article_counter = 0

    for category, articles in categorized_articles:
        # Category header color logic
        header_color = category_color(category)

        # Add category header
        content_parts.append(
            f"""
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom: 30px;">
            <tr>
                <td>
                    <table width="100%" cellpadding="12" cellspacing="0" border="0"
                    style="background-color: {header_color}; border-radius: 6px; margin-bottom: 15px;">
                        <tr>
                            <td>
                                <h2 style="color: #ffffff; margin: 0; font-size: 1.25em;
                                    font-weight: bold; line-height: 1.3;">
                                    {html.escape(category)}
                                </h2>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        """
        )

        for article in articles:
            article_counter += 1

            # Ensure the link is properly formatted with protocol
            # Handle both dict and object attribute access
            article_link = (article.get('link') if isinstance(article, dict) else article.link) or ""
            if article_link and not article_link.startswith(("http://", "https://")):
                article_link = "https://" + article_link

            # Get article attributes (compatible with both dict and object)
            article_title = (
                article.get('title') if isinstance(article, dict) else article.title
            ) or "Untitled"
            article_pubdate = (
                article.get('pubdate') if isinstance(article, dict) else article.pubdate
            ) or ""
            article_summary = (
                article.get('summary') if isinstance(article, dict) else article.summary
            ) or ""
            article_comments = (
                article.get('comments') if isinstance(article, dict)
                else getattr(article, "comments", None)
            )
            sources = (
                article.get('sources', []) if isinstance(article, dict)
                else getattr(article, "sources", [])
            )
            sources_html = _render_sources_html(sources)

            safe_link = html.escape(article_link)
            safe_title = html.escape(article_title)
            safe_pubdate = html.escape(article_pubdate)
            safe_summary = html.escape(article_summary)
            safe_comments = html.escape(article_comments) if article_comments else None

            content_parts.append(f'''
            <tr>
                <td>
                    <table width="100%" cellpadding="18" cellspacing="0" border="0"
                                 style="background-color: #f8f9fa; border-left: 4px solid #3498db;
                                 margin-bottom: 18px;">
                        <tr>
                            <td>
                                <h3 style="margin: 0 0 10px 0; line-height: 1.4; font-size: 1.1em;">
                                    <a href="{safe_link}" target="_blank"
                                    style="color: #0066cc; text-decoration: underline;">
                                    {safe_title}</a>
                                </h3>
                                <p style="margin: 0 0 12px 0; font-size: 0.875em; color: #666;">
                                    {safe_pubdate}
                                    {
                                        f' | <a href="{safe_comments}" target="_blank" '
                                        'style="color: #666; text-decoration: underline;">Comments</a>'
                                        if safe_comments else ''
                                    }
                                </p>
                                <p style="margin: 0 0 12px 0; font-size: 1em; color: #555; line-height: 1.6;">
                                {safe_summary}</p>
                                {sources_html}
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>''')

        content_parts.append("</table>")

    return "\n".join(content_parts)


@pydantic.validate_call(validate_return=True)
def _generate_claude_enhanced_html(
    filtered_items: List[Dict[str, Any]],
) -> Optional[str]:
    """Generate HTML using Claude categorization if available."""
    if not filtered_items:
        logger.info("No articles to process with Claude")
        return None
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
            # Get ordered categories
            ordered_categories = group_articles_by_priority(categorized_result)

            # Generate enhanced HTML content
            categorized_content = generate_enhanced_html_content(ordered_categories)

            # Load enhanced template - use simpler version for better email client compatibility
            try:
                template_path = files("rss_email").joinpath(
                    "email_body_enhanced_simple.html"
                )
                html_template = template_path.read_text()
            except FileNotFoundError:
                # Fallback to original enhanced template if simple version not found
                template_path = files("rss_email").joinpath("email_body_enhanced.html")
                html_template = template_path.read_text()

            # Format the template
            return html_template.format(
                subject=EMAIL_SUBJECT,
                generation_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total_articles=len(filtered_items),
                total_categories=len(ordered_categories),
                categorized_content=categorized_content,
                ai_model=os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
            )

        logger.warning(
            "Claude processing returned no results, falling back to original format"
        )
    except (ImportError, AttributeError, ValueError, json.JSONDecodeError) as e:
        logger.error("Error during Claude processing: %s", e, exc_info=True)
        logger.info(
            "Falling back to original email format due to Claude processing error"
        )

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
    try:
        claude_html = _generate_claude_enhanced_html(filtered_items)
        if claude_html:
            logger.info("Successfully generated Claude-enhanced HTML")
            return claude_html
    except Exception as e:  # pylint: disable=broad-exception-caught
        # Handle various exceptions that could occur during Claude processing
        if anthropic and isinstance(e, anthropic.APIError):
            logger.error("Claude API error: %s", e)
        elif isinstance(e, (json.JSONDecodeError, ValueError, KeyError, IndexError)):
            logger.error("Error processing Claude response: %s", e)
        else:
            logger.error("Unexpected error in Claude processing: %s", e, exc_info=True)

    # Fallback to original HTML generation
    logger.info(
        "Using original HTML generation (Claude not available, disabled, or failed)"
    )
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
            {
                f' <a href="{item["comments"]}" style="color: #ccc; font-size: 0.8em; text-decoration: none;">'
                '[Comments]</a>'
                if "comments" in item and item["comments"] else ''
            }
            </div>\n
            <section class="longdescription">{item["description"]}</section>\n"""

    html_template = files("rss_email").joinpath("email_body.html").read_text()
    return html_template.format(subject=EMAIL_SUBJECT, articles=list_output)


@pydantic.validate_call
def send_via_ses(
    to_address: str,
    from_address: str,
    subject: str,
    html_body: str,
) -> None:
    """
    Send an email via AWS SES.

    Args:
        to_address: Recipient email address
        from_address: Sender email address
        subject: Email subject line
        html_body: HTML body content
    """
    client = boto3.client("ses")

    try:
        response = client.send_email(
            Destination={"ToAddresses": [to_address]},
            Message={
                "Body": {
                    "Html": {"Charset": CHARSET, "Data": html_body},
                    "Text": {"Charset": CHARSET, "Data": html_body},
                },
                "Subject": {"Charset": CHARSET, "Data": subject},
            },
            Source=from_address,
        )
        logger.debug("Email sent! Message ID: %s", response["MessageId"])
    except ClientError as e:
        logger.warning("Failed to send email via SES: %s", e.response)
        raise


def _generate_feed_summary_html(feed_stats: Dict[str, int]) -> str:
    """Generate an HTML table of article counts per feed, sorted descending."""
    total = sum(feed_stats.values())
    rows = "".join(
        f'<tr>'
        f'<td style="padding: 4px 8px; border-bottom: 1px solid #dee2e6;">{name}</td>'
        f'<td style="padding: 4px 8px; border-bottom: 1px solid #dee2e6; text-align: right;">{count}</td>'
        f'</tr>'
        for name, count in feed_stats.items()
        if count > 0
    )
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"'
        f' style="margin-top: 30px; border-top: 2px solid #dee2e6;">'
        f'<tr><td style="padding-top: 16px;">'
        f'<p style="margin: 0 0 8px 0; font-size: 0.8em; font-weight: bold; color: #2c3e50;">'
        f'Feed breakdown &mdash; {total} articles retrieved</p>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="1"'
        f' style="border-collapse: collapse; font-size: 0.75em; color: #444;">'
        f'<tr style="background-color: #f8f9fa;">'
        f'<th style="padding: 5px 8px; text-align: left; border: 1px solid #dee2e6;">Feed</th>'
        f'<th style="padding: 5px 8px; text-align: right; border: 1px solid #dee2e6;">Articles</th>'
        f'</tr>'
        f'{rows}'
        f'</table></td></tr></table>'
    )


def create_html(
    categories: Dict[str, List[Dict[str, Any]]],
    feed_stats: Optional[Dict[str, int]] = None,
) -> str:
    """
    Create HTML email from categorized articles.

    Args:
        categories: Dictionary of category names to lists of articles
        feed_stats: Optional mapping of feed name to article count for summary section

    Returns:
        HTML string for email body
    """
    article_counter = sum(len(items) for items in categories.values())

    # Convert categories dict to list of tuples for generate_enhanced_html_content
    categorized_articles = list(categories.items())

    # Generate enhanced HTML content
    categorized_content = generate_enhanced_html_content(categorized_articles)

    # Load enhanced template
    try:
        template_path = files("rss_email").joinpath("email_body_enhanced_simple.html")
        html_template = template_path.read_text()
    except FileNotFoundError:
        template_path = files("rss_email").joinpath("email_body_enhanced.html")
        html_template = template_path.read_text()

    email_html = html_template.format(
        subject=EMAIL_SUBJECT,
        generation_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_articles=article_counter,
        total_categories=len(categories),
        categorized_content=categorized_content,
        ai_model=os.environ.get("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL),
    )

    if feed_stats:
        summary_html = _generate_feed_summary_html(feed_stats)
        email_html = email_html.replace("</body>", f"{summary_html}\n  </body>")

    return email_html


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
