"""Shared helpers for arr-style webhook handlers.

Both Sonarr and Radarr Connect webhooks ship the translate tag on a nested
object inside the payload (`series.tags` vs `movie.tags`). The tag array
shape itself is identical and varies only in element type (string vs
`{id, label}` dict) depending on which version of the arr app sent the
event. Extracted here so both handlers parse tags identically.
"""

from __future__ import annotations

from typing import Any


def has_translate_tag(payload: dict[str, Any], entity_key: str, tag_label: str) -> bool:
    """Return True iff `payload[entity_key]["tags"]` contains tag_label.

    entity_key is "movie" for Radarr or "series" for Sonarr. Tag arrays
    contain either bare strings (modern arr versions) or `{id, label}`
    dicts (older versions); both are accepted.
    """
    entity = payload.get(entity_key) or {}
    tags = entity.get("tags") or []
    for t in tags:
        if isinstance(t, str) and t == tag_label:
            return True
        if isinstance(t, dict) and t.get("label") == tag_label:
            return True
    return False
