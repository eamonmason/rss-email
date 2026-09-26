"""Tests for the shared URL helpers (credential stripping, link normalisation)."""

from datetime import datetime, timedelta

import pytest

from rss_email.retrieve_articles import get_feed
from rss_email.url_utils import normalise_link, strip_credential_params


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://stratechery.com/2026/x/?access_token=eyJabc.def",
            "https://stratechery.com/2026/x/",
        ),
        (
            "https://example.com/a?id=7&Access_Token=secret&page=2#frag",
            "https://example.com/a?id=7&page=2#frag",
        ),
        ("https://example.com/a?API_KEY=k&sig=s", "https://example.com/a"),
    ],
)
def test_strip_credential_params_removes_tokens(url, expected):
    """Credential params go (case-insensitively); other params and fragment stay."""
    assert strip_credential_params(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://news.ycombinator.com/item?id=123",
        "https://example.com/a?q=a%20b&tokenizer=x#top",
        "https://example.com/plain",
        "",
    ],
)
def test_strip_credential_params_leaves_clean_urls_byte_identical(url):
    """No credential params -> the exact input string comes back."""
    assert strip_credential_params(url) == url


def test_normalise_link_ignores_query_fragment_case_and_trailing_slash():
    """Tracking-param and trailing-slash variants of one article compare equal."""
    assert (
        normalise_link("HTTPS://Example.COM/post/1/?utm_source=rss#c")
        == normalise_link("https://example.com/post/1")
        == "https://example.com/post/1"
    )
    assert normalise_link("") == ""
    assert normalise_link("not a url") == ""


def test_get_feed_strips_access_token_from_links():
    """Ingest never stores a feed's embedded access token."""
    pub = (datetime.now() - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    feed = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Paid</title>
<item>
  <title>Daily update</title>
  <link>https://stratechery.com/2026/update/?access_token=eyJsecret</link>
  <comments>https://forum.example.com/t/1?token=abc&amp;page=1</comments>
  <pubDate>{pub}</pubDate>
  <description>d</description>
</item>
</channel></rss>""".encode()

    articles = get_feed("https://stratechery.com/feed", feed, datetime.now() - timedelta(days=1))

    assert len(articles) == 1
    assert str(articles[0].link) == "https://stratechery.com/2026/update/"
    assert str(articles[0].comments) == "https://forum.example.com/t/1?page=1"
