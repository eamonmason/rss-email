"""Discussion-thread links in the RSS Brief.

Feeds from discussion sites (Hacker News, Lobsters, Reddit) carry both an
article URL and a thread URL. These cover the brief end of that: the thread
riding through ``build_synthesis_input`` and ``build_article_index`` without
ever entering the prompt, and rendering as a trailing "discussion" link.
"""

from rss_email.brief_generator import (
    _render_article_links,
    build_article_index,
    build_prompt,
    build_synthesis_input,
)

SYNTH_CONFIG = {
    "reader_profile": "Test reader",
    "personal_interests": "Testing",
    "major_story_floor": True,
    "themed_categories": ["AI/ML"],
    "personal_categories": ["Cycling"],
    "prioritised_sources": ["Hacker News"],
    "deprioritised_sources": ["Techmeme"],
}


def test_render_article_links_appends_discussion_link():
    """An article with a thread gets a trailing discussion link."""
    index = {
        "1": {
            "title": "A story",
            "url": "https://example.com/a",
            "source": "Hacker News",
            "comments": "https://news.ycombinator.com/item?id=42",
        }
    }
    result = _render_article_links(["1"], index)
    assert 'href="https://example.com/a"' in result
    assert 'href="https://news.ycombinator.com/item?id=42"' in result
    assert ">discussion</a>" in result
    # The article link still comes first, and the citation stays one <li>.
    assert result.index("https://example.com/a") < result.index("item?id=42")
    assert result.count("<li") == 1


def test_render_article_links_omits_discussion_when_absent():
    """No thread on the feed means no extra link."""
    index = {"1": {"title": "A story", "url": "https://example.com/a", "source": "BBC"}}
    assert "discussion" not in _render_article_links(["1"], index)


def test_render_article_links_omits_self_referential_discussion():
    """A thread URL equal to the article URL would just link to itself."""
    index = {
        "1": {
            "title": "A self post",
            "url": "https://www.reddit.com/r/aws/comments/x/",
            "source": "Reddit AWS",
            "comments": "https://www.reddit.com/r/aws/comments/x/",
        }
    }
    assert "discussion" not in _render_article_links(["1"], index)


def test_render_article_links_discussion_on_unlinked_article():
    """An article with no URL still gets its discussion link."""
    index = {
        "1": {
            "title": "A story",
            "url": "",
            "source": "Lobsters",
            "comments": "https://lobste.rs/s/abc",
        }
    }
    result = _render_article_links(["1"], index)
    assert 'href="https://lobste.rs/s/abc"' in result
    assert ">discussion</a>" in result


def test_render_article_links_escapes_discussion_url():
    """Discussion URLs are escaped like every other interpolated value."""
    index = {
        "1": {
            "title": "A story",
            "url": "https://example.com/a",
            "source": "HN",
            "comments": 'https://x/?a=1&b="2"',
        }
    }
    result = _render_article_links(["1"], index)
    assert '"2"' not in result
    assert "&amp;b=&quot;2&quot;" in result


def test_build_synthesis_input_carries_comments():
    """The discussion URL survives the reduction to synthesis input."""
    categories = {
        "AI/ML": [
            {
                "title": "A",
                "link": "https://x/a",
                "summary": "sa",
                "comments": "https://news.ycombinator.com/item?id=7",
            }
        ]
    }
    result = build_synthesis_input(categories, ["AI/ML"], [])
    assert result["AI/ML"][0]["comments"] == "https://news.ycombinator.com/item?id=7"


def test_build_article_index_carries_comments():
    """...and into the index the renderer reads from."""
    synthesis_input = {
        "AI/ML": [
            {
                "title": "A",
                "url": "https://x/a",
                "summary": "",
                "source": "HN",
                "comments": "https://news.ycombinator.com/item?id=7",
                "id": "1",
            }
        ]
    }
    index = build_article_index(synthesis_input)
    assert index["1"]["comments"] == "https://news.ycombinator.com/item?id=7"


def test_discussion_url_never_enters_the_prompt():
    """URLs are deliberately kept out of the prompt; threads are no exception."""
    synthesis_input = build_synthesis_input(
        {
            "AI/ML": [
                {
                    "title": "A",
                    "link": "https://x/a",
                    "summary": "sa",
                    "comments": "https://news.ycombinator.com/item?id=7",
                }
            ]
        },
        ["AI/ML"],
        [],
    )
    prompt = build_prompt(synthesis_input, SYNTH_CONFIG)
    assert "news.ycombinator.com" not in prompt
    assert "https://x/a" not in prompt
