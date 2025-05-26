"""Test suite for JSON repair functionality to validate handling of truncated JSON data."""

import json
import logging

from rss_email.json_repair import repair_truncated_json

# Set up logging
logging.basicConfig(level=logging.INFO)


def test_repair():
    """Test the JSON repair functionality with various cases of truncated JSON."""
    # Test with valid JSON
    valid_json = '{"categories": {"Tech": [{"id": "article_1"}]}}'
    result = repair_truncated_json(valid_json)
    print(f"Valid JSON result: {json.dumps(result)}")

    # Test with truncated JSON (unclosed string)
    truncated_json = '{"categories": {"Tech": [{"id": "article_1", "title": "Test'
    result = repair_truncated_json(truncated_json)
    print(f"Truncated JSON repair: {json.dumps(result) if result else 'Failed'}")

    # Test with another common truncation pattern (unclosed array/object)
    another_truncated = (
        '{"categories": {"Tech": [{"id": "article_1"}], "News": [{"id": "article_2"'
    )
    result = repair_truncated_json(another_truncated)
    print(f"Another truncated repair: {json.dumps(result) if result else 'Failed'}")

    # Test with complex nested structure
    complex_truncated = '{"categories": {"Tech": [{"id": "art_1"}, {"id": "art_2", "links": ["http://example.com'
    result = repair_truncated_json(complex_truncated)
    print(f"Complex nested repair: {json.dumps(result) if result else 'Failed'}")

    # Test with malformed property name
    property_truncated = '{"categories": {"Tech": [{"id": "article_1"}], "Ne'
    result = repair_truncated_json(property_truncated)
    print(f"Property truncated repair: {json.dumps(result) if result else 'Failed'}")


if __name__ == "__main__":
    test_repair()
