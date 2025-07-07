"""Utility functions for JSON extraction and processing."""

import html
import json
import logging
import re
from typing import Any, Dict, List, Optional

import regex
import pydantic

logger = logging.getLogger(__name__)


@pydantic.validate_call
def extract_json_from_text(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Extract valid JSON from text that might contain additional content.
    """
    # First, try direct parsing of the entire text as JSON
    text = text.strip()
    try:
        parsed = json.loads(text)
        if is_valid_json_object(parsed, required_fields):
            parsed = process_html_entities_in_json(parsed)
            logger.info("Successfully parsed complete JSON response directly")
            return parsed
    except json.JSONDecodeError:
        pass

    # Continue with existing extraction methods

    # Log the first 100 chars of the response for debugging
    logger.debug("Response text begins with: %s", text[:100] if text else "None")

    # Try different extraction methods
    json_data = extract_json_using_regex(text, required_fields)
    if json_data:
        return process_html_entities_in_json(json_data)

    json_data = extract_json_with_bracket_balancing(text, required_fields)
    if json_data:
        return process_html_entities_in_json(json_data)

    # Try applying fixes to the JSON before parsing
    json_data = extract_json_with_common_fixes(text, required_fields)
    if json_data:
        return process_html_entities_in_json(json_data)

    # Try a more aggressive approach to find JSON objects
    json_data = extract_json_aggressive(text, required_fields)
    if json_data:
        return process_html_entities_in_json(json_data)

    # If we got here, extraction failed
    # Log a portion of the text that failed parsing
    # sample_text = text[:500] + "..." if text and len(text) > 500 else text
    # logger.error("Failed to extract valid JSON. Text sample: %s", sample_text)
    logger.error("Failed to extract valid JSON. Text: %s", text)
    return None


@pydantic.validate_call(validate_return=True)
def process_html_entities_in_json(json_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process HTML entities in title and summary fields throughout the JSON object.

    Args:
        json_obj: JSON object to process

    Returns:
        Dict[str, Any]: Processed JSON object
    """
    if not isinstance(json_obj, dict):
        return json_obj

    # Process all dictionary values recursively
    for key, value in json_obj.items():
        if isinstance(value, dict):
            json_obj[key] = process_html_entities_in_json(value)
        elif isinstance(value, list):
            # For lists, process each item if it's a dict, or check if it's a string that needs unescaping
            processed_items = []
            for item in value:
                if isinstance(item, dict):
                    processed_items.append(process_html_entities_in_json(item))
                elif isinstance(item, str) and key in ["title", "summary"]:
                    processed_items.append(html.unescape(item))
                else:
                    processed_items.append(item)
            json_obj[key] = processed_items
        elif key in ["title", "summary"] and isinstance(value, str):
            # Decode HTML entities in title and summary fields
            json_obj[key] = html.unescape(value)

    return json_obj


def extract_json_aggressive(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Use aggressive methods to extract JSON from potentially malformed text.
    Specifically handling HTML fragments that might be included in the response.

    Args:
        text: Text that may contain JSON
        required_fields: List of fields that must be present in the JSON object

    Returns:
        Optional[Dict[str, Any]]: Parsed JSON or None if parsing failed
    """
    if not text:
        return None

    # Preprocess input to handle HTML entities that might break JSON parsing
    # Don't decode entities yet, but ensure they're properly escaped in JSON context
    processed_text = text

    # Remove any HTML tags that might be present in the response
    # This helps when Claude includes explanatory HTML in its response
    text_no_html = re.sub(r"<[^>]+>", "", processed_text)

    # Try bracket matching first (which is more precise)
    bracket_count = 0
    start_positions = []
    extracted_jsons = []

    for i, char in enumerate(text_no_html):
        if char == "{":
            if bracket_count == 0:
                start_positions.append(i)
            bracket_count += 1
        elif char == "}":
            bracket_count -= 1
            if bracket_count == 0 and start_positions:
                # Found a complete JSON object
                start = start_positions.pop()
                potential_json = text_no_html[start: i + 1]
                try:
                    parsed = json.loads(potential_json)
                    if is_valid_json_object(parsed, required_fields):
                        logger.info(
                            "Successfully extracted JSON using bracket matching"
                        )
                        extracted_jsons.append(parsed)
                except json.JSONDecodeError:
                    continue

    if extracted_jsons:
        # Return the longest/most complete JSON object found
        return max(extracted_jsons, key=lambda x: len(json.dumps(x)))

    # If bracket matching failed, try simpler approaches

    # Find the first { and the last } in the text to extract what might be our JSON
    start_idx = text_no_html.find("{")
    if start_idx == -1:
        return None

    end_idx = text_no_html.rfind("}")
    if end_idx == -1:
        return None

    potential_json = text_no_html[start_idx: end_idx + 1]

    try:
        parsed = json.loads(potential_json)
        if is_valid_json_object(parsed, required_fields):
            logger.info("Successfully extracted JSON using aggressive method")
            return parsed
    except json.JSONDecodeError:
        pass

    # Try one more approach - find all JSON-like structures and try the longest ones first
    potential_jsons = re.findall(r"\{[^{}]*(\{[^{}]*\})*[^{}]*\}", text_no_html)
    potential_jsons.sort(key=len, reverse=True)  # Try longest first

    for json_str in potential_jsons:
        try:
            parsed = json.loads(json_str)
            if is_valid_json_object(parsed, required_fields):
                logger.info("Successfully extracted JSON from potential fragments")
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def extract_json_with_common_fixes(
    text: str, required_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    Apply common fixes to malformed JSON text before attempting to parse.

    Args:
        text: Text that may contain JSON
        required_fields: List of fields that must be present in the JSON object

    Returns:
        Optional[Dict[str, Any]]: Parsed JSON or None if parsing failed
    """
    if not text:
        return None

    # Try to find JSON between triple backticks first
    backtick_matches = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    for match in backtick_matches:
        try:
            parsed = json.loads(match)
            if is_valid_json_object(parsed, required_fields):
                logger.info(
                    "Successfully extracted JSON from code block after normalization"
                )
                return parsed
        except json.JSONDecodeError:
            pass

    # Handle HTML entities that might be causing issues
    # We don't want to decode them all (which could break valid JSON syntax)
    # But we need to handle the ones that interfere with JSON structure
    text_fixed_entities = text
    structural_entities = {
        "&quot;": '"',  # These affect JSON structure
        "&apos;": "'",
    }

    for entity, replacement in structural_entities.items():
        text_fixed_entities = text_fixed_entities.replace(entity, replacement)

    # Try parsing with fixed structural entities
    try:
        parsed = json.loads(text_fixed_entities)
        if is_valid_json_object(parsed, required_fields):
            logger.info("Successfully parsed JSON after fixing structural entities")
            return parsed
    except json.JSONDecodeError:
        pass

    # Common fixes to try
    potential_fixes = [
        # Original text
        text,
        # Fixed structural entities
        text_fixed_entities,
        # Fix trailing commas in arrays/objects
        re.sub(r",\s*([}\]])", r"\1", text),
        # Fix missing quotes around keys
        re.sub(r"(\{|\,)\s*([a-zA-Z0-9_]+)\s*:", r'\1"\2":', text),
        # Fix single quotes to double quotes
        re.sub(r"\'([^\']*?)\'(\s*:)", r'"\1"\2', text),
        # Remove non-JSON text before the first {
        text[text.find("{"):] if "{" in text else text,
        # Remove non-JSON text after the last }
        text[: text.rfind("}") + 1] if "}" in text else text,
        # Remove potential HTML tags that might be in the response
        re.sub(r"<[^>]+>", "", text),
    ]

    for fixed_text in potential_fixes:
        try:
            # If the fix worked, return the parsed JSON
            parsed = json.loads(fixed_text)
            if is_valid_json_object(parsed, required_fields):
                logger.info("Successfully parsed JSON after applying fixes")
                return parsed
        except json.JSONDecodeError:
            continue

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
                potential = text[start: i + 1]
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


@pydantic.validate_call(validate_return=True)
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
