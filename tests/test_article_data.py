"""Exercise the response shape iterator used by the two-stage flow."""

from rss_email.article_processor import _iter_category_entries


def test_iter_category_entries_dict_shape():
    """Dict-shaped categories return (category, entry) pairs."""
    data = {
        "categories": {
            "Technology": [
                {
                    "group_id": "group_0",
                    "title": "Test Article 1",
                    "summary": "Summary 1",
                    "category": "Technology",
                },
                {
                    "group_id": "group_1",
                    "title": "Test Article 2",
                    "summary": "Summary 2",
                    "category": "Technology",
                },
            ]
        },
        "group_count": 2,
        "verification": "processed_all_groups",
    }

    pairs = _iter_category_entries(data)

    categories = [c for c, _ in pairs]
    assert categories == ["Technology", "Technology"]
    assert all(entry["group_id"] for _, entry in pairs)


def test_iter_category_entries_list_shape():
    """List-shaped categories also yield (category, entry) pairs."""
    data = {
        "categories": [
            {
                "name": "Technology",
                "groups": [
                    {"group_id": "group_0", "title": "T1", "summary": "S1"},
                ],
            },
            {
                "name": "Science",
                "articles": [
                    {"group_id": "group_1", "title": "T2", "summary": "S2"},
                ],
            },
        ]
    }

    pairs = _iter_category_entries(data)

    assert [c for c, _ in pairs] == ["Technology", "Science"]


def test_iter_category_entries_ignores_metadata_keys():
    """Top-level metadata fields are not treated as categories."""
    data = {
        "categories": {
            "Technology": [{"group_id": "group_0", "title": "T", "summary": "S"}],
            "group_count": 1,
            "verification": "processed_all_groups",
        }
    }

    pairs = _iter_category_entries(data)

    assert [c for c, _ in pairs] == ["Technology"]


def test_iter_category_entries_invalid_value_returns_empty():
    """Non-list values for a category are skipped silently."""
    data = {"categories": {"Technology": 42}}

    pairs = _iter_category_entries(data)

    assert not pairs
