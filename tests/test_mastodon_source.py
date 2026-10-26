"""Tests for the Mastodon home-timeline article source."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest

from rss_email import mastodon_source as ms

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
CONFIG = ms.MastodonConfig(base_url="https://mastodon.example", max_articles=2)


def make_status(
    sid="1", url="https://example.com/a", title="Title", age_hours=1,
    boosts=3, favs=3, followers=100, **extra
):
    created = NOW - timedelta(hours=age_hours)
    status = {
        "id": sid,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "url": f"https://mastodon.example/@user/{sid}",
        "content": "<p>Worth a read</p>",
        "reblogs_count": boosts,
        "favourites_count": favs,
        "account": {"acct": "user", "followers_count": followers},
        "card": {"url": url, "title": title, "description": "About it"},
    }
    status.update(extra)
    return status


class TestConfig:
    def test_missing_block_disables(self):
        assert ms.load_config({"feeds": []}) is None

    def test_disabled_block(self):
        raw = {"mastodon": {"base_url": "https://m.example", "enabled": False}}
        assert ms.load_config(raw) is None

    def test_invalid_block_is_ignored(self):
        assert ms.load_config({"mastodon": {"base_url": "not a url"}}) is None

    def test_valid_block(self):
        config = ms.load_config({"mastodon": {"base_url": "https://m.example"}})
        assert config.name == "Mastodon" and config.max_articles == 15


class TestScore:
    def test_no_engagement_scores_zero(self):
        assert ms.score(make_status(boosts=0, favs=0)) == 0.0

    def test_no_followers_scores_zero(self):
        assert ms.score(make_status(followers=0)) == 0.0

    def test_small_account_beats_large_at_equal_engagement(self):
        assert ms.score(make_status(followers=10)) > ms.score(make_status(followers=10000))


class TestWanted:
    @pytest.mark.parametrize(
        "extra", [{"reblogged": True}, {"favourited": True}, {"bookmarked": True},
                  {"filtered": [{"filter": {}}]}, {"card": None},
                  {"card": {"url": "https://x.example", "title": "  "}}],
    )
    def test_rejects(self, extra):
        assert not ms._wanted(make_status(**extra))

    def test_accepts_link_post(self):
        assert ms._wanted(make_status())


class TestSelect:
    def test_maps_card_and_discussion_link(self):
        (article,) = ms.select_articles([make_status()], CONFIG, NOW)
        assert str(article.link) == "https://example.com/a"
        assert str(article.comments) == "https://mastodon.example/@user/1"
        assert article.title == "Title"
        assert "@user: Worth a read" in article.description
        assert article.source_name == "Mastodon"

    def test_dedupes_by_link_keeping_best_score(self):
        low = make_status("1", boosts=1, favs=1)
        high = make_status("2", boosts=50, favs=50)
        (article,) = ms.select_articles([low, high], CONFIG, NOW)
        assert str(article.comments).endswith("/2")

    def test_boost_uses_inner_status_and_boost_time(self):
        inner = make_status("9", age_hours=40)
        boost = make_status("10", age_hours=2, reblog=inner)
        (article,) = ms.select_articles([boost], CONFIG, NOW)
        assert str(article.comments).endswith("/9")
        assert article.pubdate == (NOW - timedelta(hours=2)).replace(tzinfo=None)

    def test_pubdate_sorts_against_naive_feed_articles(self):
        (article,) = ms.select_articles([make_status()], CONFIG, NOW)
        feed_item = ms.RSSItem(
            title="t", link="https://example.com/x", pubdate=datetime(2026, 10, 21, 9, 0)
        )
        assert sorted([article, feed_item], reverse=True)[0] is article

    def test_cap_applies_per_24h_window(self):
        recent = [make_status(str(i), url=f"https://e.com/{i}", age_hours=1) for i in range(4)]
        older = [make_status(str(i + 10), url=f"https://e.com/{i + 10}", age_hours=30)
                 for i in range(4)]
        assert len(ms.select_articles(recent + older, CONFIG, NOW)) == 4  # 2 per window

    def test_skips_unusable_card(self):
        bad = make_status(url="ftp://[bad")
        assert ms.select_articles([bad], CONFIG, NOW) == []


class TestFetch:
    def _client_patch(self, handler):
        real = httpx.Client
        return patch.object(
            ms.httpx, "Client",
            lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
        )

    def test_pages_until_since_and_sends_token(self):
        pages = {
            None: [make_status("30", age_hours=1), make_status("29", age_hours=5)],
            "29": [make_status("28", age_hours=10), make_status("27", age_hours=50)],
        }
        seen = []

        def handler(request):
            seen.append(request.headers["authorization"])
            return httpx.Response(200, json=pages[request.url.params.get("max_id")])

        since = NOW - timedelta(hours=24)
        with self._client_patch(handler):
            result = ms.fetch_home_timeline("https://mastodon.example/", "tok", since)
        assert [s["id"] for s in result] == ["30", "29", "28"]
        assert set(seen) == {"Bearer tok"}

    def test_empty_page_stops(self):
        with self._client_patch(lambda r: httpx.Response(200, json=[])):
            assert ms.fetch_home_timeline("https://m.example", "t", NOW) == []


class TestRetrieve:
    FEED = {"mastodon": {"base_url": "https://mastodon.example"}}

    def test_not_configured(self):
        assert ms.retrieve_mastodon_articles({}, NOW) == (None, [])

    def test_missing_token_disables_quietly(self):
        with patch.object(ms, "get_token", return_value=None):
            assert ms.retrieve_mastodon_articles(self.FEED, NOW) == ("Mastodon", [])

    def test_api_failure_returns_empty(self):
        with patch.object(ms, "get_token", return_value="t"), patch.object(
            ms, "fetch_home_timeline", side_effect=httpx.ConnectError("boom")
        ):
            assert ms.retrieve_mastodon_articles(self.FEED, NOW) == ("Mastodon", [])

    def test_success_selects_articles(self):
        with patch.object(ms, "get_token", return_value="t"), patch.object(
            ms, "fetch_home_timeline", return_value=[make_status(age_hours=0.1)]
        ):
            name, articles = ms.retrieve_mastodon_articles(self.FEED, NOW - timedelta(days=1))
        assert name == "Mastodon" and len(articles) == 1
