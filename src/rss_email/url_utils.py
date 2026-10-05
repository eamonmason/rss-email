"""Small URL helpers shared by ingest and the RSS Brief.

``strip_credential_params`` removes credential-like query parameters (e.g. a
personal ``access_token`` some paid feeds embed in every article link) so
they never reach S3, the digest, the brief, or a forwarded email.
``normalise_link`` reduces a URL to a comparison key for cross-day dedupe.
"""

from __future__ import annotations

import urllib.parse

from w3lib.url import url_query_cleaner

# Query parameter names (compared case-insensitively) that carry credentials.
CREDENTIAL_PARAMS = frozenset(
    {
        "access_token",
        "token",
        "auth",
        "auth_token",
        "api_key",
        "apikey",
        "key",
        "jwt",
        "session",
        "sid",
        "sig",
        "signature",
    }
)


def strip_credential_params(url: str) -> str:
    """Return ``url`` without credential-like query parameters.

    Matching is case-insensitive; the actual key spellings found in the URL are
    handed to ``w3lib.url.url_query_cleaner`` for removal (it matches exactly).
    A URL with no such parameters is returned unchanged, byte for byte.
    """
    if not url or "?" not in url:
        return url
    query = urllib.parse.urlsplit(url).query
    names = {
        name
        for name, _ in urllib.parse.parse_qsl(query, keep_blank_values=True)
        if name.lower() in CREDENTIAL_PARAMS
    }
    if not names:
        return url
    return url_query_cleaner(url, sorted(names), remove=True, keep_fragments=True)


def normalise_link(url: str) -> str:
    """Reduce a URL to ``scheme://host/path`` for "same article" comparisons.

    Drops the query string and fragment, lower-cases the scheme and host, and
    strips a trailing slash, so tracking-parameter variants of one article
    compare equal. Returns ``""`` for an empty or host-less URL.
    """
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    if not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"
