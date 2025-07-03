"""Utility module for repairing and handling truncated JSON data."""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _fix_comma_delimiters(json_str: str) -> str:
    """
    Fix common comma delimiter issues in JSON strings.

    Args:
        json_str: JSON string with potential comma issues

    Returns:
        str: Fixed JSON string
    """
    # Common patterns to fix:
    # 1. Missing commas between object properties
    # 2. Extra commas before closing braces/brackets
    # 3. Missing commas between array elements

    fixed = json_str

    # Fix missing commas between adjacent string properties
    # Pattern: "key": "value" "nextkey": "nextvalue"
    fixed = re.sub(r'"\s*([}])\s*"([^"]+)":', r'"\1, "\2":', fixed)

    # Fix missing commas between object properties
    # Pattern: "value" "key":
    fixed = re.sub(r'"\s+(["])', r'", \1', fixed)

    # Fix missing commas between array elements
    # Pattern: } {
    fixed = re.sub(r'}\s+{', r'}, {', fixed)

    # Fix missing commas between string values in arrays
    # Pattern: "value" "value"
    fixed = re.sub(r'"\s+"([^"]+)":', r'", "\1":', fixed)

    # Remove trailing commas before closing braces/brackets
    fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)

    return fixed


# pylint: disable=too-many-branches
def repair_truncated_json(json_str: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to repair a truncated JSON string by balancing braces and quotes.

    Args:
        json_str: Potentially truncated JSON string

    Returns:
        Dict or None: Repaired JSON as dict if successful, None otherwise
    """
    if not json_str or not json_str.strip():
        logger.error("Empty or whitespace-only JSON string provided")
        return None
    try:
        # First try direct parsing
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.info("Attempting to repair truncated JSON: %s", e)
        # Handle specific comma delimiter errors
        if "Expecting ',' delimiter" in str(e):
            logger.info("Attempting to fix comma delimiter issue")
            # Try to fix common comma issues
            fixed_str = _fix_comma_delimiters(json_str)
            if fixed_str != json_str:
                try:
                    return json.loads(fixed_str)
                except json.JSONDecodeError:
                    logger.warning("Comma delimiter fix didn't work, trying general repair")
                    # Continue with general repair below
            else:
                logger.warning("No comma delimiter fixes applied, trying general repair")

        # Make a copy of the original string for repair
        repaired = json_str

        # Track if string is inside quotes
        in_string = False
        # Track nesting levels
        stack = []

        # First pass - analyze structure and identify truncation point
        for i, char in enumerate(json_str):
            if char == '"' and (i == 0 or json_str[i - 1] != "\\"):
                in_string = not in_string

            if not in_string:
                if char in ("{", "["):
                    stack.append(char)
                elif char == "}":
                    if stack and stack[-1] == "{":
                        stack.pop()
                    else:
                        # Unbalanced closing brace
                        logger.warning("Unbalanced closing brace at position %s", i)
                elif char == "]":
                    if stack and stack[-1] == "[":
                        stack.pop()
                    else:
                        # Unbalanced closing bracket
                        logger.warning("Unbalanced closing bracket at position %s", i)

        # Handle unclosed string at the end
        if in_string:
            repaired += '"'

        # Close any open structures in reverse order
        for bracket in reversed(stack):
            if bracket == "{":
                repaired += "}"
            elif bracket == "[":
                repaired += "]"

        # Special case - if truncated in the middle of a property name or value
        if '"' in repaired and repaired.rstrip("{}[]").split('"')[-1].strip(" ,"):
            parts = repaired.split('"')
            # If we have an odd number of quote marks, add null value for the last property
            if len(parts) % 2 == 0:
                last_part = parts[-1]
                if ":" not in last_part:
                    repaired = repaired.rstrip("}]") + ":null" + repaired[-1]

        try:
            return json.loads(repaired)
        except json.JSONDecodeError as repair_error:
            # More advanced repair attempt
            logger.warning("First repair attempt failed: %s", repair_error)

            # Try more aggressive repair
            try:
                # Find the last valid JSON object pattern
                pattern = r"(\{(?:[^{}]|(?:\{[^{}]*\}))*)"
                match = re.search(pattern, json_str)
                if match:
                    partial = match.group(1)
                    # Count unclosed braces
                    open_count = partial.count("{")
                    close_count = partial.count("}")
                    missing = open_count - close_count

                    # Add missing closing braces
                    if missing > 0:
                        partial += "}" * missing

                    return json.loads(partial)
            except json.JSONDecodeError:
                logger.error("Failed to repair JSON after multiple attempts")
                return None

            logger.error("Failed to repair JSON after attempts")
            return None
