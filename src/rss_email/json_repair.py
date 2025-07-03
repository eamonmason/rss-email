"""Utility module for repairing and handling truncated JSON data."""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


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
    except json.JSONDecodeError:
        logger.info("Attempting to repair truncated JSON")

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
        except json.JSONDecodeError as e:
            # More advanced repair attempt
            logger.warning("First repair attempt failed: %s", e)

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
