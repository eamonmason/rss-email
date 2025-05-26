"""Utility module for compressing and decompressing JSON data using gzip and base64 encoding."""

import base64
import gzip
import json
from typing import Any, Dict, Optional


def compress_json(data: Dict[str, Any]) -> str:
    """
    Compress JSON data to a base64 encoded string.

    Args:
        data: Dictionary to compress

    Returns:
        str: Base64 encoded compressed string
    """
    # Convert dict to JSON string
    json_str = json.dumps(data)
    # Compress using gzip
    compressed = gzip.compress(json_str.encode("utf-8"))
    # Convert to base64 for safe text transmission
    return base64.b64encode(compressed).decode("ascii")


def decompress_json(compressed_str: str) -> Optional[Dict[str, Any]]:
    """
    Decompress a base64 encoded compressed JSON string back to a dictionary.

    Args:
        compressed_str: Base64 encoded compressed string

    Returns:
        Dict[str, Any]: Decompressed JSON data or None if decompression fails
    """
    try:
        # Decode base64
        decoded = base64.b64decode(compressed_str)
        # Decompress gzip
        decompressed = gzip.decompress(decoded)
        # Parse JSON
        return json.loads(decompressed.decode("utf-8"))
    except (json.JSONDecodeError, ValueError, IOError):
        print("Error decompressing JSON")
        return None
