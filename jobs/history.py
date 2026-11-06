"""Mechanical posting history shared by "copy job", the agent API and MCP.

Only locally observed facts are reported: dates, sources, locations and whether
a posting appeared in the latest successful fetch. Nothing here judges whether a
job is genuine, the same position or still open; that is left to the reader or
their AI. Related postings are the other rows of the stored duplicate group
(same employer, similar title), which analyze maintains.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

ZONE = "Australia/Melbourne"
RELATED_LIMIT = 5
BASIS = "same_dedupe_group"
SCOPE_NOTE = ("Local collection records. Related postings are grouped automatically by employer and "
              "title similarity and may be different actual positions; not appearing in later fetches "
              "may only mean the search no longer matched, not that the job closed. Verify against the "
              "original listing and the employer's own information.")

_COLUMNS = "uid,source,title,location,listing_date,first_seen_at,last_seen_at,dedupe_group"


def latest_successful_fetch(conn) -> str | None:
    """Start of the newest fetch batch in which every source succeeded."""
    try:
        row = conn.execute("SELECT MAX(started_at) FROM fetch_batches "
                           "WHERE collection_state='success'").fetchone()
    except sqlite3.OperationalError:   # pre-batch database: no reference point
        return None
    return row[0] if row and row[0] else None


def group_index(conn) -> dict[str, list[dict]]:
    """All grouped rows keyed by group, for building many records at once."""
    index: dict[str, list[dict]] = {}
    for row in conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE dedupe_group IS NOT NULL"):
        index.setdefault(row["dedupe_group"], []).append(dict(row))
    return index


def _group_rows(conn, group: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"SELECT {_COLUMNS} FROM jobs WHERE dedupe_group=?", (group,))]


def _seen_in(last_seen: str | None, latest: str | None) -> bool | None:
    if not latest or not last_seen:
        return None
    return str(last_seen) >= str(latest)


def build(row: dict, members: list[dict] | None, latest: str | None) -> dict:
    """History facts for one job; `members` is its stored group (None if unknown)."""
    related = None
    total = None
    if members is not None:
        others = [m for m in members if m["uid"] != row["uid"]]
        others.sort(key=lambda m: (str(m.get("first_seen_at") or ""), m["uid"]), reverse=True)
        total = len(others)
        related = [{"uid": m["uid"], "source": m.get("source"), "title": m.get("title"),
                    "location": m.get("location"), "listing_date": m.get("listing_date"),
                    "first_seen_at": m.get("first_seen_at"), "last_seen_at": m.get("last_seen_at"),
                    "seen_in_latest_successful_fetch": _seen_in(m.get("last_seen_at"), latest)}
                   for m in others[:RELATED_LIMIT]]
    return {"latest_successful_fetch_at": latest,
            "seen_in_latest_successful_fetch": _seen_in(row.get("last_seen_at"), latest),
            "related_basis": BASIS,
            "related_total": total,
            "related": related,
            "scope_note": SCOPE_NOTE}


def for_job(conn, row: dict) -> dict:
    group = row.get("dedupe_group")
    members = _group_rows(conn, group) if group else None
    return build(row, members, latest_successful_fetch(conn))


def local_date(value) -> str:
    """Melbourne calendar date of a stored UTC timestamp; plain dates pass through."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        stamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if stamp.tzinfo is None:
        return stamp.date().isoformat()
    return stamp.astimezone(ZoneInfo(ZONE)).date().isoformat()
