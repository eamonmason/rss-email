#!/usr/bin/env python3
"""
CLI tool to generate the companion RSS Brief locally.

Retrieves articles from S3, categorises them with Claude (reusing the digest
pipeline), synthesises the brief, and either writes the HTML to a file
(``--dry-run``) for iteration or sends it via SES.
"""

import argparse
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Optional

import dotenv
from botocore.exceptions import ClientError

# Add the src directory to the Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# These imports depend on sys.path modification
from rss_email.article_processor import (  # noqa: E402 # pylint: disable=wrong-import-position
    ClaudeRateLimiter,
    process_articles_with_claude,
)
from rss_email.brief_generator import (  # noqa: E402 # pylint: disable=wrong-import-position
    generate_brief,
)
from rss_email.email_articles import (  # noqa: E402 # pylint: disable=wrong-import-position
    filter_items,
    get_last_run,
    read_s3_file,
    send_via_ses,
)


def setup_logging(debug: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )


def get_run_date(days: Optional[int], parameter_name: Optional[str]) -> datetime:
    """Get the run date either from days ago or parameter store."""
    if days is not None:
        return datetime.now() - timedelta(days=days)
    if parameter_name:
        return get_last_run(parameter_name)
    return datetime.now() - timedelta(days=3)  # Default 3 days


def generate(
    bucket: str,
    key: str,
    run_date: datetime,
    *,
    dry_run: bool,
    output_file: str,
    debug: bool,
) -> None:
    """Categorise articles, synthesise the brief, and write or send it."""
    try:
        logging.info("Reading articles JSON from s3://%s/%s", bucket, key)
        rss_content = read_s3_file(bucket, key)
        filtered_items = filter_items(rss_content, run_date)

        if not filtered_items:
            logging.info("No new articles found since %s", run_date)
            return

        logging.info("Found %s articles since %s", len(filtered_items), run_date)

        logging.info("Categorising articles with Claude...")
        rate_limiter = ClaudeRateLimiter()
        result = process_articles_with_claude(filtered_items, rate_limiter)
        if not result:
            logging.error("Failed to categorise articles with Claude")
            return

        article_count = sum(len(items) for items in result.categories.values())
        today = datetime.now().strftime("%Y-%m-%d")

        logging.info("Synthesising RSS Brief...")
        brief_html = generate_brief(
            result.categories, date=today, article_count=article_count
        )
        if not brief_html:
            logging.error("Brief generation returned no content")
            return

        if dry_run:
            with open(output_file, "w", encoding="utf-8") as handle:
                handle.write(brief_html)
            logging.info("Wrote brief HTML to %s", output_file)
        else:
            source_email = os.environ["SOURCE_EMAIL_ADDRESS"]
            to_email = os.environ["TO_EMAIL_ADDRESS"]
            send_via_ses(to_email, source_email, f"RSS Brief — {today}", brief_html)
            logging.info("Sent RSS Brief to %s", to_email)

    except ClientError as e:
        logging.error("AWS error: %s", e)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        logging.error("Error generating brief: %s", e)
        if debug:
            traceback.print_exc()


def main() -> None:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Generate the companion RSS Brief email locally"
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        help="Number of days of articles to process (overrides parameter store)",
    )
    parser.add_argument(
        "--bucket", "-b", help="S3 bucket name (defaults to BUCKET environment variable)"
    )
    parser.add_argument(
        "--key", "-k", help="S3 key for articles JSON file (defaults to KEY environment variable)"
    )
    parser.add_argument(
        "--parameter",
        "-p",
        help="Parameter store name for last run (defaults to LAST_RUN_PARAMETER env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the brief HTML to a file instead of sending it",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        default="brief_output.html",
        help="File to write the brief HTML to in --dry-run mode",
    )
    parser.add_argument("--debug", action="store_true", help="Show debug information")
    parser.add_argument(
        "--env-file", type=str, help="Path to .env file to load environment variables"
    )

    args = parser.parse_args()

    if args.env_file:
        dotenv.load_dotenv(args.env_file)
        print(f"Loaded environment variables from {args.env_file}")

    setup_logging(args.debug)

    bucket = args.bucket or os.environ.get("BUCKET")
    key = args.key or os.environ.get("KEY")
    parameter_name = args.parameter or os.environ.get("LAST_RUN_PARAMETER")

    missing_config = []
    if not bucket:
        missing_config.append("S3 bucket (--bucket or BUCKET env var)")
    if not key:
        missing_config.append("S3 key (--key or KEY env var)")
    if missing_config:
        logging.error("Missing required configuration: %s", ", ".join(missing_config))
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get(
        "ANTHROPIC_API_KEY_PARAMETER"
    ):
        logging.error(
            "Anthropic API key is not configured. Set ANTHROPIC_API_KEY environment "
            "variable or ANTHROPIC_API_KEY_PARAMETER for Parameter Store."
        )
        sys.exit(1)

    run_date = get_run_date(args.days, parameter_name)
    generate(
        bucket,
        key,
        run_date,
        dry_run=args.dry_run,
        output_file=args.output_file,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
