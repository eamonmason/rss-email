"""Test to debug article processor data structure issues."""

import logging
import os
import sys
from rss_email.article_processor import _create_categorized_articles

# Add the src directory to the path so we can import the module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

# Enable debugging output
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

print("Importing modules...")

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_categorized_data_structure():
    """Test that handles both valid and invalid article data structures."""
    print("Starting test_categorized_data_structure...")
    # Mock articles data
    mock_articles = [
        {
            "title": "Test Article 1",
            "description": "Description 1",
            "link": "https://example.com/1",
        },
        {
            "title": "Test Article 2",
            "description": "Description 2",
            "link": "https://example.com/2",
        },
    ]
    print(f"Mock articles created: {len(mock_articles)}")

    # Mock usage stats
    mock_usage_stats = {"processed_at": "2025-06-18T12:00:00", "tokens_used": 1000}

    # Case 1: Valid structure with dictionary categories
    valid_dict_structure = {
        "categories": {
            "Technology": [
                {
                    "id": "article_0",
                    "title": "Test Article 1",
                    "link": "https://example.com/1",
                    "summary": "Summary 1",
                    "category": "Technology",
                    "pubdate": "Mon, 18 Jun 2025",
                    "related_articles": [],
                },
                {
                    "id": "article_1",
                    "title": "Test Article 2",
                    "link": "https://example.com/2",
                    "summary": "Summary 2",
                    "category": "Technology",
                    "pubdate": "Mon, 18 Jun 2025",
                    "related_articles": [],
                },
            ]
        }
    }

    result1 = _create_categorized_articles(
        valid_dict_structure, mock_articles, mock_usage_stats
    )
    assert result1 is not None
    assert "Technology" in result1.categories
    assert len(result1.categories["Technology"]) == 2

    # Case 2: Invalid structure - integer instead of list
    invalid_structure = {
        "categories": {
            "Technology": 42  # Integer instead of list of articles
        }
    }

    result2 = _create_categorized_articles(
        invalid_structure, mock_articles, mock_usage_stats
    )
    assert result2 is not None
    assert (
        len(result2.categories) == 0
    )  # Should handle the error and return empty categories

    # Case 3: Valid structure with list categories
    valid_list_structure = {
        "categories": [
            {
                "name": "Technology",
                "articles": [
                    {
                        "id": "article_0",
                        "title": "Test Article 1",
                        "link": "https://example.com/1",
                        "summary": "Summary 1",
                        "category": "Technology",
                        "pubdate": "Mon, 18 Jun 2025",
                        "related_articles": [],
                    }
                ],
            },
            {
                "name": "Science",
                "articles": [
                    {
                        "id": "article_1",
                        "title": "Test Article 2",
                        "link": "https://example.com/2",
                        "summary": "Summary 2",
                        "category": "Science",
                        "pubdate": "Mon, 18 Jun 2025",
                        "related_articles": [],
                    }
                ],
            },
        ]
    }

    result3 = _create_categorized_articles(
        valid_list_structure, mock_articles, mock_usage_stats
    )
    assert result3 is not None
    assert "Technology" in result3.categories
    assert "Science" in result3.categories
    assert len(result3.categories["Technology"]) == 1
    assert len(result3.categories["Science"]) == 1

    # Case 4: Invalid list structure - integer instead of article list
    invalid_list_structure = {
        "categories": [
            {
                "name": "Technology",
                "articles": 123,  # Integer instead of list
            }
        ]
    }

    result4 = _create_categorized_articles(
        invalid_list_structure, mock_articles, mock_usage_stats
    )
    assert result4 is not None

    # Case 5: Missing or malformed categorized_data
    result5 = _create_categorized_articles({}, mock_articles, mock_usage_stats)
    assert result5 is not None
    assert len(result5.categories) == 0

    print("All tests complete")
