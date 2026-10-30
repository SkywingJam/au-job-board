"""Location search over fixed display representatives; no location persistence."""
from __future__ import annotations

from . import labels as labels_mod, locations, report, skills

GEO_KEYS = ("geo",)
TIER_ORDER = ("exact", "near", "unknown")


def enabled(filters):
    return bool(filters.get("geo"))


def decorate(conn, row, legacy_labels, index, *, target=None):
    row = report.decorate([dict(row)], legacy_labels, index)[0]
    row["_geo_match"] = (locations.match_tier(
        locations.parse_listing(row.get("location")), target)
        if target is not None else None)
    return row


def find(conn, config, uid, filters=None):
    if not uid:
        return None
    kept, excluded, labels, index = report.load(conn, config, deduplicate=False)
    row = next((item for item in kept + excluded if item["uid"] == uid), None)
    if row is None:
        return None
    # Keep the requested UID, including non-representatives and excluded peers.
    # Summarize the complete group with the same source/location tags as lists.
    group = row.get("dedupe_group") or row["uid"]
    own = f"{row['source']}@{row.get('location') or '-'}"
    others = [f"{item['source']}@{item.get('location') or '-'}"
              for item in kept + excluded
              if (item.get("dedupe_group") or item["uid"]) == group]
    others = [tag for tag in others if tag != own]
    row["duplicate_count"] = len(others)
    row["duplicate_sources"] = "; ".join(dict.fromkeys(others))
    target = locations.parse_target(filters["geo"]) if (filters or {}).get("geo") else None
    return decorate(conn, row, labels, index, target=target)


def _view_pool(conn, view, kept, excluded):
    if view == "excluded":
        return excluded
    if view == "recommend":
        return kept
    return kept + excluded


def _scope_groups(conn, view, pool, filters, candidate_uids):
    """Return groups represented in the selected source scope."""
    from . import db

    if candidate_uids is not None:
        selected = [row for row in pool if row["uid"] in candidate_uids]
    elif view == "new":
        batch = db.last_batch_started_at(conn)
        selected = ([row for row in pool if (row.get("first_seen_at") or "") >= batch]
                    if batch else [])
    else:
        selected = pool
    return {row.get("dedupe_group") or row["uid"] for row in selected}


def query(conn, config, view, offset, limit, filters, since, skills_cfg, *,
          candidate_uids=None):
    from . import panel_data

    kept, excluded, labels, index = report.load(conn, config, deduplicate=False)
    pool = _view_pool(conn, view, kept, excluded)
    # Scope membership is a property of the source candidates, independent of
    # which excluded/eligible side supplies the view representative.
    in_scope = _scope_groups(conn, view, kept + excluded, filters, candidate_uids)

    # Choose one representative from the complete view pool, then retain groups
    # whose source candidates belong to the requested batch/scoped query.
    # The excluded audit keeps every source record, as without location search.
    representatives = pool if view == "excluded" else report.best_per_group(pool, config)
    representatives = [
        row for row in representatives
        if (row.get("dedupe_group") or row["uid"]) in in_scope
    ]

    frozen_labels = (
        labels_mod.action_states_at(conn, since or labels_mod.freeze_marker(conn))
        if view == "recommend" else labels
    )
    target = locations.parse_target(filters["geo"])
    terms = skills.search_terms(filters["q"], skills_cfg or {}) if filters.get("q") else []
    rows = []
    for row in representatives:
        label = report.label_of(row, frozen_labels, index)
        has_record = report.has_labels(row, frozen_labels, index)
        if view == "recommend":
            if label.get("action") or panel_data.is_stale(row):
                continue
        elif view == "labelled" and not has_record:
            continue
        if terms and not all(rx.search(panel_data._search_text(row, label))
                             for rx, _, _ in terms):
            continue
        if not panel_data.passes_filters(row, label, filters, has_record):
            continue
        tier = locations.match_tier(locations.parse_listing(row.get("location")), target)
        if tier == "hidden":
            continue
        row["_geo_match"] = tier
        rows.append(row)

    ordered = []
    sort_view = "all" if view == "excluded" and not filters.get("sort") else view
    # Effective qualification (when requested) outranks location; each
    # qualification zone keeps the CBD / metro / fallback blocks and the user's sort.
    sink = panel_data.ineligible_last(view, filters)
    for row in rows:
        row["_ineligible_last"] = panel_data.sink_rank(row, labels, index) if sink else 0
    for zone in ((0, 1) if sink else (0,)):
        for tier in TIER_ORDER:
            members = [row for row in rows
                       if row["_geo_match"] == tier and row["_ineligible_last"] == zone]
            ordered.extend(panel_data._ordered(members, sort_view, filters))

    total = len(ordered)
    page = [
        decorate(conn, row, labels, index, target=target)
        for row in ordered[offset:offset + limit]
    ]
    return page, total
