#!/usr/bin/env python3
"""
CLI tool to process RSS articles with Claude without sending emails.

This script allows developers to test the RSS article processing functionality
locally by retrieving articles from S3 and running them through the Claude API.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from botocore.exceptions import ClientError

# Add the src directory to the Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from rss_email.article_processor import (
    ClaudeRateLimiter,
    group_articles_by_priority,
    process_articles_with_claude,
)
from rss_email.email_articles import filter_items, get_last_run, read_s3_file


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


def process_articles(
    bucket: str, key: str, run_date: datetime, output_format: str, debug: bool
) -> None:
    """
    Process articles from S3 using Claude API and output the results.

    Args:
        bucket: S3 bucket name
        key: S3 key for RSS file
        run_date: Date to filter articles from
        output_format: Output format (json, text, summary)
        debug: Whether to show debug info
    """
    try:
        # Read and parse RSS file from S3
        logging.info(f"Reading RSS file from s3://{bucket}/{key}")
        rss_content = read_s3_file(bucket, key)
        filtered_items = filter_items(rss_content, run_date)

        if not filtered_items:
            logging.info("No new articles found since %s", run_date)
            return

        logging.info(f"Found {len(filtered_items)} articles since {run_date}")

        # Process with Claude
        logging.info("Processing articles with Claude...")
        rate_limiter = ClaudeRateLimiter()
        result = process_articles_with_claude(filtered_items, rate_limiter)

        if not result:
            logging.error("Failed to process articles with Claude")
            return

        # Show results based on output format
        if output_format == "json":
            print(json.dumps(result.model_dump(), indent=2))
        elif output_format == "text":
            # Print categories and articles in text format
            print("\n===== PROCESSED ARTICLES =====\n")
            ordered_categories = group_articles_by_priority(result)
            for category_name, articles in ordered_categories:
                print(f"\n== {category_name} ({len(articles)} articles) ==\n")
                for i, article in enumerate(articles, 1):
                    print(f"{i}. {article.title}")
                    print(f"   Link: {article.link}")
                    print(f"   Summary: {article.summary}")
                    if article.related_articles:
                        related_ids = ", ".join(article.related_articles)
                        print(f"   Related: {related_ids}")
                    print()
        elif output_format == "summary":
            # Print just the stats
            stats = result.processing_metadata
            print("\n===== PROCESSING SUMMARY =====\n")
            print(f"Processed at: {stats['processed_at']}")
            print(f"Articles processed: {stats['articles_count']}")
            print(f"Categories found: {len(result.categories)}")
            print(f"Tokens used: {stats['tokens_used']}")
            print(f"Processing time: {stats['processing_time_seconds']:.2f} seconds\n")

            print("Category distribution:")
            ordered_categories = group_articles_by_priority(result)
            for category_name, articles in ordered_categories:
                print(f"- {category_name}: {len(articles)} articles")

    except ClientError as e:
        logging.error(f"AWS error: {e}")
    except Exception as e:
        logging.error(f"Error processing articles: {e}")
        if debug:
            import traceback

            traceback.print_exc()


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Process RSS articles with Claude API without sending emails"
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        help="Number of days of articles to process (overrides parameter store)",
    )
    parser.add_argument(
        "--bucket",
        "-b",
        help="S3 bucket name (defaults to BUCKET environment variable)",
    )
    parser.add_argument(
        "--key", "-k", help="S3 key for RSS file (defaults to KEY environment variable)"
    )
    parser.add_argument(
        "--parameter",
        "-p",
        help="Parameter store name for last run (defaults to LAST_RUN_PARAMETER environment variable)",
    )
    parser.add_argument(
        "--output",
        "-o",
        choices=["json", "text", "summary"],
        default="summary",
        help="Output format (json, text, or summary)",
    )
    parser.add_argument("--debug", action="store_true", help="Show debug information")
    parser.add_argument(
        "--env-file", type=str, help="Path to .env file to load environment variables"
    )

    args = parser.parse_args()

    # Load environment variables from .env file if specified
    if args.env_file:
        try:
            import dotenv

            dotenv.load_dotenv(args.env_file)
            print(f"Loaded environment variables from {args.env_file}")
        except ImportError:
            print(
                "python-dotenv package not installed. Install with: pip install python-dotenv"
            )
            sys.exit(1)

    # Set up logging
    setup_logging(args.debug)

    # Get configuration from arguments or environment variables
    bucket = args.bucket or os.environ.get("BUCKET")
    key = args.key or os.environ.get("KEY")
    parameter_name = args.parameter or os.environ.get("LAST_RUN_PARAMETER")

    # Validate required configuration
    missing_config = []
    if not bucket:
        missing_config.append("S3 bucket (--bucket or BUCKET env var)")
    if not key:
        missing_config.append("S3 key (--key or KEY env var)")

    if missing_config:
        logging.error("Missing required configuration: %s", ", ".join(missing_config))
        sys.exit(1)

    # Check if API key is available
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get(
        "ANTHROPIC_API_KEY_PARAMETER"
    ):
        logging.error(
            "Anthropic API key is not configured. Set ANTHROPIC_API_KEY environment variable "
            "or ANTHROPIC_API_KEY_PARAMETER for Parameter Store."
        )
        sys.exit(1)

    # Get run date based on arguments
    run_date = get_run_date(args.days, parameter_name)

    # Process articles
    process_articles(bucket, key, run_date, args.output, args.debug)


if __name__ == "__main__":
    main()
