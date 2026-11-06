"""Mechanical posting history shared by "copy job", the agent API and MCP.

Only locally observed facts are reported: dates, sources, locations and whether
a posting appeared in the latest successful fetch. Nothing here judges whether a
job is genuine, the same position or still open; that is left to the reader or
their AI. Related postings are the other rows of the stored duplicate group
(same employer, similar title), which analyze maintains.
"""

from __future__ import annotations

import datetime as dt
import json
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


def fetch_reference(conn) -> dict:
    """Per source, the newest fully successful batch that searched it, with exact members.

    A partial batch, or one that searched other sources only (`fetch --only`),
    never becomes the reference for a source it did not cover successfully.
    """
    by_source: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT b.batch_id, b.started_at, r.stats FROM fetch_batches b "
            "JOIN runs r ON r.run_id = b.fetch_run_id "
            "WHERE b.collection_state='success' ORDER BY b.started_at DESC, b.batch_id DESC").fetchall()
    except sqlite3.OperationalError:   # pre-batch database: no reference point
        return {"by_source": {}, "members": {}}
    for batch_id, started_at, stats in rows:
        try:
            covered = json.loads(stats or "{}")
        except ValueError:
            continue
        if not isinstance(covered, dict):
            continue
        for source, result in covered.items():
            if isinstance(result, dict) and "error" not in result:
                by_source.setdefault(source, {"batch_id": batch_id, "started_at": started_at})
    members: dict[str, set] = {}
    for ref in by_source.values():
        if ref["batch_id"] not in members:
            members[ref["batch_id"]] = {r[0] for r in conn.execute(
                "SELECT uid FROM fetch_batch_members WHERE batch_id=?", (ref["batch_id"],))}
    return {"by_source": by_source, "members": members}


def _reference_for(source, reference: dict) -> dict | None:
    return reference["by_source"].get(source)


def _seen_in(uid: str, source, reference: dict) -> bool | None:
    ref = _reference_for(source, reference)
    if ref is None:
        return None
    return uid in reference["members"].get(ref["batch_id"], set())


def group_index(conn) -> dict[str, list[dict]]:
    """All grouped rows keyed by group, for building many records at once."""
    index: dict[str, list[dict]] = {}
    for row in conn.execute(f"SELECT {_COLUMNS} FROM jobs WHERE dedupe_group IS NOT NULL"):
        index.setdefault(row["dedupe_group"], []).append(dict(row))
    return index


def _group_rows(conn, group: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"SELECT {_COLUMNS} FROM jobs WHERE dedupe_group=?", (group,))]


def build(row: dict, members: list[dict] | None, reference: dict) -> dict:
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
                    "seen_in_latest_successful_fetch": _seen_in(m["uid"], m.get("source"), reference)}
                   for m in others[:RELATED_LIMIT]]
    ref = _reference_for(row.get("source"), reference)
    return {"latest_successful_fetch_at": ref["started_at"] if ref else None,
            "seen_in_latest_successful_fetch": _seen_in(row["uid"], row.get("source"), reference),
            "related_basis": BASIS,
            "related_total": total,
            "related": related,
            "scope_note": SCOPE_NOTE}


def for_job(conn, row: dict) -> dict:
    group = row.get("dedupe_group")
    members = _group_rows(conn, group) if group else None
    return build(row, members, fetch_reference(conn))


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
