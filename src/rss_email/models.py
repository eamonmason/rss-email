"""Shared pydantic models for RSS Email application."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl

# Single source of truth for the default Claude model ID, so every module
# that falls back to it (submit_email_batch, submit_podcast_batch,
# article_grouper, article_processor, podcast_generator, email_articles)
# stays in sync instead of repeating (and drifting on) the same literal.
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"


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
    source_name: Optional[str] = Field(default=None, alias="sourceName")
    source_url: Optional[HttpUrl] = Field(default=None, alias="sourceUrl")

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
    max_articles: Optional[int] = None
    lookback_days: Optional[int] = None

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

        return cls(
            name=name,
            url=url,
            enabled=enabled,
            max_articles=data.get("max_articles"),
            lookback_days=data.get("lookback_days"),
        )


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
    claude_model: str = DEFAULT_CLAUDE_MODEL
    claude_max_tokens: int = 100000
    claude_max_requests: int = 10
    anthropic_api_key_parameter: str = "rss-email-anthropic-api-key"

    # Processing Configuration
    days_of_news: int = 3
    description_max_length: int = 400

    model_config = {
        "arbitrary_types_allowed": True,
    }


class ArticleSource(BaseModel):
    """A single feed's coverage of a logical article."""

    feed_name: Optional[str] = None
    feed_url: Optional[str] = None
    title: str
    link: str
    pubdate: str
    comments: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}


class ClaudeResponse(BaseModel):
    """Expected structure for Claude API responses."""

    categories: Dict[str, List[Dict[str, Any]]]
    article_count: int
    verification: str
    processing_metadata: Optional[Dict[str, Any]] = None

    model_config = {
        "arbitrary_types_allowed": True,
    }


class BriefTheme(BaseModel):
    """A single synthesised theme within a category for the RSS Brief."""

    theme: str
    signal_strength: Literal["HIGH", "STRATEGIC", "GENERAL"]
    tldr: str = ""
    top_articles: List[str] = Field(default_factory=list)
    relevance_to_reader: Optional[str] = None


class BriefCategory(BaseModel):
    """A category's synthesised verdict and themes for the RSS Brief."""

    week_verdict: str = ""
    themes: List[BriefTheme] = Field(default_factory=list)


class CrossCuttingSignal(BaseModel):
    """A signal that spans multiple categories in the RSS Brief."""

    signal: str = ""
    categories_involved: List[str] = Field(default_factory=list)
    implication: str = ""


class PersonalBlock(BaseModel):
    """The personal-interest digest block (e.g. Cycling) of the RSS Brief."""

    top_stories: List[str] = Field(default_factory=list)
    summary: str = ""


class BriefSynthesis(BaseModel):
    """Validated structure of the Claude synthesis response for the RSS Brief."""

    categories: Dict[str, BriefCategory] = Field(default_factory=dict)
    cross_cutting: List[CrossCuttingSignal] = Field(default_factory=list)
    personal: Optional[PersonalBlock] = None
