"""Tests for podcast script prompt generation."""

from rss_email.podcast_generator import create_podcast_script_prompt


def test_create_podcast_script_prompt_basic():
    """Test basic podcast script prompt generation."""
    articles = [
        {"title": "Article 1", "description": "Description 1"},
        {"title": "Article 2", "description": "Description 2"},
    ]

    prompt = create_podcast_script_prompt(articles)

    # Verify prompt contains article information
    assert "Article 1" in prompt
    assert "Description 1" in prompt
    assert "Article 2" in prompt
    assert "Description 2" in prompt

    # Verify prompt contains instruction text
    assert "Marco" in prompt
    assert "Joanna" in prompt


def test_create_podcast_script_prompt_empty():
    """Test podcast script prompt with no articles."""
    articles = []

    prompt = create_podcast_script_prompt(articles)

    # Should still contain instruction text but no articles
    assert "Marco" in prompt
    assert "Joanna" in prompt
    assert "Title:" not in prompt.split("\n\n")[-1]  # No article section


def test_create_podcast_script_prompt_single_article():
    """Test podcast script prompt with single article."""
    articles = [
        {
            "title": "Breaking Tech News",
            "description": "Major development in AI",
        }
    ]

    prompt = create_podcast_script_prompt(articles)

    # Verify article is included
    assert "Breaking Tech News" in prompt
    assert "Major development in AI" in prompt

    # Verify separator is present
    assert "---" in prompt


def test_create_podcast_script_prompt_special_characters():
    """Test podcast script prompt with special characters in articles."""
    articles = [
        {
            "title": "Article with 'quotes' and \"double quotes\"",
            "description": "Description with <html> & special chars",
        }
    ]

    prompt = create_podcast_script_prompt(articles)

    # Verify special characters are preserved
    assert "quotes" in prompt
    assert "double quotes" in prompt
    assert "<html>" in prompt
    assert "&" in prompt


def test_create_podcast_script_prompt_multiple_articles():
    """Test podcast script prompt with multiple articles."""
    articles = [
        {"title": f"Article {i}", "description": f"Description {i}"}
        for i in range(10)
    ]

    prompt = create_podcast_script_prompt(articles)

    # Verify all articles are included
    for i in range(10):
        assert f"Article {i}" in prompt
        assert f"Description {i}" in prompt

    # Verify correct number of separators (one after each article)
    assert prompt.count("---") == 10


def test_create_podcast_script_prompt_missing_fields():
    """Test podcast script prompt with missing article fields."""
    articles = [
        {"title": "Article 1"},  # Missing description
        {"description": "Description 2"},  # Missing title
        {},  # Empty article
    ]

    prompt = create_podcast_script_prompt(articles)

    # Should handle missing fields gracefully
    assert "Article 1" in prompt
    assert "Description 2" in prompt
    assert "None" in prompt  # get() returns None for missing keys


def test_create_podcast_script_prompt_structure():
    """Test podcast script prompt has correct structure."""
    articles = [
        {"title": "Test Article", "description": "Test Description"}
    ]

    prompt = create_podcast_script_prompt(articles)

    # Verify structure
    lines = prompt.split("\n")

    # Should have header text followed by article section
    assert any("Marco" in line or "Joanna" in line for line in lines)

    # Article section should be at the end
    article_section = prompt.split("\n\n")[-1]
    assert "Title: Test Article" in article_section
    assert "Description: Test Description" in article_section
