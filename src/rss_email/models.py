"""Shared pydantic models for RSS Email application."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class RSSItem(BaseModel):
    """
    RSS Item model for consistent handling across the application.
    This model handles both raw RSS items and processed articles.
    """

    title: str = Field(min_length=1)
    link: HttpUrl
    description: Optional[str] = ""
    comments: Optional[HttpUrl] = None
    pubdate: datetime = Field(alias="pubDate")
    sort_date: Optional[float] = Field(default=None, alias="sortDate")

    model_config = {
        "populate_by_name": True,  # Allow both field names and aliases
        "arbitrary_types_allowed": True,
    }

    def model_post_init(self, __context) -> None:  # pylint: disable=arguments-differ
        """Calculate sort_date from pubdate if not provided."""
        if self.sort_date is None:
            self.sort_date = self.pubdate.timestamp()  # pylint: disable=no-member

    def __lt__(self, other):
        return self.pubdate < other.pubdate


class FeedConfig(BaseModel):
    """Configuration for a single RSS feed."""

    name: str = Field(min_length=1)
    url: HttpUrl
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FeedConfig:
        """Create FeedConfig from dictionary, handling _url suffix for disabled feeds."""
        name = data.get("name", "")
        enabled = True

        # Check if this is a disabled feed (using "_url" instead of "url")
        if "_url" in data:
            url = data.get("_url", "")
            enabled = False
        else:
            url = data.get("url", "")
            enabled = True

        # Handle legacy pattern where name ends with "_url"
        if name.endswith("_url"):
            name = name[:-4]  # Remove "_url" suffix
            enabled = False

        return cls(name=name, url=url, enabled=enabled)


class FeedList(BaseModel):
    """List of RSS feed configurations."""

    feeds: List[FeedConfig]

    @classmethod
    def from_json_data(cls, data: Dict[str, Any]) -> FeedList:
        """Create FeedList from JSON data, handling both enabled and disabled feeds."""
        feeds_data = data.get("feeds", [])
        feeds = []

        for feed_data in feeds_data:
            feeds.append(FeedConfig.from_dict(feed_data))

        return cls(feeds=feeds)


class EmailSettings(BaseModel):
    """Email configuration settings."""

    source_email_address: str = Field(min_length=1)
    to_email_address: str = Field(min_length=1)
    email_recipients: Optional[str] = None
    subject: str = "Daily News"
    charset: str = "UTF-8"


class ApplicationSettings(BaseModel):
    """Application-wide settings."""

    # S3 Configuration
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    last_run_parameter: str = "rss-email-lastrun"

    # Email Configuration
    email: EmailSettings

    # Claude Configuration
    claude_enabled: bool = True
    claude_model: str = "claude-haiku-4-5-20251001"
    claude_max_tokens: int = 100000
    claude_max_requests: int = 10
    anthropic_api_key_parameter: str = "rss-email-anthropic-api-key"

    # Processing Configuration
    days_of_news: int = 3
    description_max_length: int = 400

    model_config = {
        "arbitrary_types_allowed": True,
    }


class ClaudeResponse(BaseModel):
    """Expected structure for Claude API responses."""

    categories: Dict[str, List[Dict[str, Any]]]
    article_count: int
    verification: str
    processing_metadata: Optional[Dict[str, Any]] = None

    model_config = {
        "arbitrary_types_allowed": True,
    }
