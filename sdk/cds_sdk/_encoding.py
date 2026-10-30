"""A215: URL-encoding policy shared by ``client.py`` and ``stream.py``.

The SDK used to splice caller-supplied identifiers straight into URL
templates (``f"/api/v1/sandbox-sessions/{session_id}"``). An id containing
a reserved character could then escape its route segment — ``"../../admin"``
or ``"x/execute"`` silently re-route the request to a different endpoint —
or corrupt a query string.

The server declares every one of these parameters as a plain (non
``:path``) FastAPI segment, so ``quote(..., safe="")`` is exact: ordinary
ids (UUIDs, hex, slugs; letters/digits/``._-~``) round-trip byte-identical,
while ``/``, ``%``, ``?``, ``#`` and friends can no longer move the route.
httpx does NOT percent-encode interpolated path text itself — it only
encodes params/headers — hence the explicit helper at every site.
"""
from __future__ import annotations

from urllib.parse import quote

__all__ = ["path_segment", "query_value"]


def path_segment(value: object) -> str:
    """Percent-encode one value destined for a single URL path segment."""
    return quote(str(value), safe="")


def query_value(value: object) -> str:
    """Percent-encode one value interpolated into a query string.

    Only needed for hand-built query strings (the WS stream URL); ordinary
    request params go through httpx ``params=``, which encodes for us.
    """
    return quote(str(value), safe="")
