"""Pure helpers — no panel imports, fully unit-testable."""
from __future__ import annotations

import re

_USERNAME_CLEAN = re.compile(r"[^a-z0-9_]")


def sanitize_fragment(value: str) -> str:
    value = (value or "").lower().strip().lstrip("@")
    value = _USERNAME_CLEAN.sub("", value)
    return value or "user"


def allocate_username(prefix: str, tg_username: str | None, tg_id: int, existing: set[str]) -> str:
    """prefix-tgusername, with 2/3/4 suffix on collision.

    existing: set of already-taken usernames (shop subs + panel users).
    """
    clean_prefix = sanitize_fragment(prefix)[:24] or "shop"
    clean_user = sanitize_fragment(tg_username or f"id{tg_id}")[:32]
    base = f"{clean_prefix}-{clean_user}"
    if base not in existing:
        return base
    i = 2
    while f"{base}{i}" in existing:
        i += 1
    return f"{base}{i}"


def traffic_ratio(used: int | None, limit: int | None) -> float | None:
    if not limit or limit <= 0 or used is None:
        return None
    return used / limit
