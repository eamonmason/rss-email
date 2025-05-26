"""Utility functions for JSON extraction and processing."""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import regex

logger = logging.getLogger(__name__)


def extract_json_from_text(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Extract valid JSON from text that might contain additional content.

    Args:
        text: Text that may contain JSON
        required_fields: List of fields that must be present in the JSON object

    Returns:
        Optional[Dict[str, Any]]: Extracted JSON dict or None if extraction failed
    """
    # First try direct parsing
    try:
        parsed = json.loads(text)
        if is_valid_json_object(parsed, required_fields):
            logger.info("Successfully parsed complete JSON response directly")
            return parsed
    except json.JSONDecodeError:
        pass  # Continue with extraction methods

    # Try different extraction methods
    json_data = extract_json_using_regex(text, required_fields)
    if json_data:
        return json_data

    json_data = extract_json_with_bracket_balancing(text, required_fields)
    if json_data:
        return json_data

    # If we got here, extraction failed
    logger.error("Failed to extract valid JSON from text")
    return None


def extract_json_with_bracket_balancing(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Extract JSON by manually balancing brackets.

    Args:
        text: Text that may contain JSON
        required_fields: List of fields that must be present in the JSON object

    Returns:
        Optional[Dict[str, Any]]: Extracted JSON dict or None if extraction failed
    """
    candidates = []

    # Try to find JSON by bracket balancing
    start_positions = [i for i, c in enumerate(text) if c == "{"]
    for start in start_positions:
        json_candidate = extract_json_at_position(text, start)
        if json_candidate and is_valid_json_object(json_candidate, required_fields):
            candidates.append(json_candidate)

    if candidates:
        # Return the first valid candidate
        logger.info("Successfully extracted JSON using bracket balancing")
        return candidates[0]

    return None


def extract_json_at_position(text: str, start: int) -> Optional[Dict[str, Any]]:
    """
    Extract JSON starting at the given position by balancing brackets.

    Args:
        text: Text that may contain JSON
        start: Starting position of the opening brace

    Returns:
        Optional[Dict[str, Any]]: Extracted JSON dict or None if extraction failed
    """
    bracket_count = 0

    for i in range(start, len(text)):
        if text[i] == "{":
            bracket_count += 1
        elif text[i] == "}":
            bracket_count -= 1

            if bracket_count == 0:
                # Found potential complete JSON
                potential = text[start : i + 1]
                try:
                    return json.loads(potential)
                except json.JSONDecodeError:
                    pass

                # Stop looking further for this starting position
                break

    return None


def extract_json_using_regex(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Extract JSON using regex patterns.

    Args:
        text: Text that may contain JSON
        required_fields: List of fields that must be present in the JSON object

    Returns:
        Optional[Dict[str, Any]]: Extracted JSON dict or None if extraction failed
    """

    # Look for JSON between triple backticks (common LLM formatting)
    backtick_json = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if backtick_json:
        try:
            candidate = backtick_json.group(1).strip()
            parsed = json.loads(candidate)
            if is_valid_json_object(parsed, required_fields):
                logger.info("Successfully extracted JSON from code block")
                return parsed
        except json.JSONDecodeError:
            pass

    try:
        # Pattern to find JSON objects
        json_pattern = r"\{(?:[^{}]|(?R))*\}"
        matches = regex.findall(json_pattern, text)

        candidates = []
        for match in sorted(matches, key=len, reverse=True):
            try:
                parsed = json.loads(match)
                if is_valid_json_object(parsed, required_fields):
                    candidates.append(parsed)
            except json.JSONDecodeError:
                continue

        if candidates:
            # Return the first valid candidate
            logger.info("Successfully extracted JSON using regex pattern")
            return candidates[0]

    except (ImportError, AttributeError) as e:
        logger.debug("Advanced regex failed: %s, falling back to basic approach", e)

    return None


def is_valid_json_object(obj: Any, required_fields: Optional[List[str]] = None) -> bool:
    """
    Check if an object is a valid JSON dictionary with required fields.

    Args:
        obj: Object to validate
        required_fields: List of fields that must be present

    Returns:
        bool: True if the object is valid, False otherwise
    """
    if not isinstance(obj, dict):
        return False

    if required_fields:
        return all(field in obj for field in required_fields)

    return True
