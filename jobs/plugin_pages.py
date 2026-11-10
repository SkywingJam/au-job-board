"""Core-owned read-only page resources and bounded summary snapshots.

Installed plugin Python is never imported here. Browser pages receive only the
declared DTO. Core query semantics are shared with the ordinary panel.
"""
from __future__ import annotations

from contextlib import closing
import gzip
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import re
import time
from urllib.parse import quote

from . import RunTimeout, labels, location_query, locations, panel_data, plugin_host, report, run_state, skills
from .agent_api import APIError, SnapshotStore, read_connection, utcnow
from .config import db_path

MAX_ROWS = 20000
MAX_ASSET = 8 * 1024 * 1024
MAX_REFERENCE = 12 * 1024 * 1024
MIN_REUSE_TTL = 120     # seconds a reused snapshot must still have left
MAX_HANDED = 10
MAX_UID = 512
MAX_HANDED_BYTES = 4 * 1024 * 1024


def require_page(config, plugin_id):
    """The validated manifest of an authorized page or sidebar plugin."""
    manifest = plugin_host.display_manifest(config, plugin_id)
    if manifest is None:
        raise APIError('plugin_disabled_or_permission_required', 403)
    return manifest


def _require_list(config, plugin_id):
    """Query snapshots and reference files belong to the map profile only."""
    manifest = require_page(config, plugin_id)
    if plugin_host.handed(manifest):
        raise APIError('permission_not_granted', 403)
    return manifest


def frame_entry(manifest, slot=None):
    """Path under web/ for a frame slot; no slot keeps the top-level entry."""
    if slot is None:
        entry = manifest['entry']
    elif slot in ('sidebar', 'page') and isinstance(manifest.get(slot), dict):
        entry = manifest[slot]['entry']
    else:
        raise APIError('not_found', 404)
    return entry.removeprefix('web/')


def frame_csp(manifest, base):
    """Frame CSP from the manifest profile: only declared network targets are
    external origins, so a `network: none` plugin gets none."""
    external = ''.join(f' https://{target}' for entry in manifest['permissions']['network']
                       for target in entry['targets'])
    return ("default-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts; "
            f"script-src {base} blob:; style-src {base} 'unsafe-inline'; "
            f"worker-src {base} blob: data:; connect-src {base}{external}; "
            f"img-src {base}{external} data: blob:; font-src {base}{external}; frame-ancestors 'self'")


def resource(config, plugin_id, relative):
    """Regular declared web files only, with no symlink/path escape."""
    require_page(config, plugin_id)
    if (not isinstance(relative, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]+', relative)
            or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise APIError('invalid_asset', 404)
    directory = plugin_host.plugin_dir(config, plugin_id)
    if directory is None:
        raise APIError('invalid_asset', 404)
    base = directory / 'web'
    path = base / relative
    current = path
    while current != base.parent:
        if current.is_symlink():
            raise APIError('invalid_asset', 404)
        current = current.parent
    if not path.is_file() or path.stat().st_size > MAX_ASSET:
        raise APIError('invalid_asset', 404)
    mime = (mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
    if path.suffix in ('.js', '.mjs'):
        mime = 'text/javascript'
    if path.suffix not in ('.html', '.js', '.mjs', '.css', '.txt', '.json', '.png', '.svg', '.woff2'):
        raise APIError('invalid_asset', 404)
    return path.read_bytes(), mime + ('; charset=utf-8' if mime.startswith('text/') else '')


def reference(config, plugin_id, filename):
    """Verify public package bytes before decompression; never read private data."""
    _require_list(config, plugin_id)
    directory = plugin_host.plugin_dir(config, plugin_id)
    if directory is None:
        raise APIError('reference_unavailable', 503)
    base = directory / 'reference'
    if base.is_symlink():
        raise APIError('reference_unavailable', 503)
    index = plugin_host._read(base / 'index.json', 2 * 1024 * 1024)
    if filename == 'index.json':
        return index
    files = {f['path']: f['sha256'] for f in index.get('files', [])
             if isinstance(f, dict) and set(f) >= {'path', 'sha256'}}
    if not isinstance(filename, str) or not re.fullmatch(r'poa-[0-9]{1,4}\.geojson\.gz', filename) or filename not in files:
        raise APIError('invalid_reference', 400)
    path = base / filename
    if path.is_symlink() or base.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REFERENCE:
        raise APIError('reference_unavailable', 503)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != files[filename]:
        raise APIError('reference_integrity', 503)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(MAX_REFERENCE + 1)
        if len(decoded) > MAX_REFERENCE:
            raise ValueError('reference too large')
        return json.loads(decoded)
    except (ValueError, OSError, EOFError):
        raise APIError('reference_integrity', 503) from None


def _summary(row, order, link):
    """The declared `job_fields` DTO, shared by map snapshots and handed jobs."""
    uid = row['uid']
    return {
        'uid': uid, 'group_id': row.get('dedupe_group') or uid,
        'title': row.get('title') or '', 'company': row.get('company') or '',
        'location_raw': row.get('location') or '',
        'location_fact': locations.parse_listing(row.get('location')),
        'salary': row.get('salary') or '', 'work_type': row.get('work_type') or '',
        'listing_date': row.get('listing_date'), 'total_score': row.get('total_score'),
        'priority': bool((row.get('_label') or {}).get('priority')),
        'core_order': order,
        'panel_url': link + '#' + quote(uid, safe=''),
    }


def revision(row):
    """First 16 hex of sha256 over title, company, location, salary and description."""
    text = json.dumps([row.get(k) or '' for k in ('title', 'company', 'location', 'salary', 'description')],
                      ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _handed_query(qs):
    if set(qs) - {'uid', 'detail'} or qs.get('detail', ['1']) != ['1']:
        raise APIError('invalid_query', 400)
    uids = qs.get('uid') or []
    if not 0 < len(uids) <= MAX_HANDED:
        raise APIError('invalid_uid_count', 400)
    if any(not uid or len(uid) > MAX_UID for uid in uids):
        raise APIError('invalid_uid', 400)
    if len(set(uids)) != len(uids):
        raise APIError('duplicate_uid', 400)
    return uids, 'detail' in qs


def handed_jobs(config, plugin_id, qs):
    """Jobs the user handed to a sidebar plugin, read in one read-only transaction.
    Only the handed-jobs profile may call it; detail needs a declared `job_detail`."""
    manifest = require_page(config, plugin_id)
    if not plugin_host.handed(manifest):
        raise APIError('permission_not_granted', 403)
    uids, detail = _handed_query(qs)
    if detail and 'job_detail' not in manifest['permissions']:
        raise APIError('detail_not_granted', 403)
    deadline = time.monotonic() + 12
    link = panel_data._link('all')
    with closing(read_connection(db_path(config))) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        # The same rows and decoration the agent API's job detail uses.
        kept, excluded, legacy, index = report.load(conn, config, deduplicate=False)
        wanted = set(uids)
        found = {row['uid']: row for row in kept + excluded if row['uid'] in wanted}
        items = []
        for order, uid in enumerate(uids):
            if uid not in found:
                items.append({'uid': uid, 'missing': True})
                continue
            row = location_query.decorate(conn, found[uid], legacy, index, snippets=detail)
            merged = row.get('_label') or {}
            item = {**_summary(row, order, link), 'revision': revision(row),
                    'human_labels': {'eligibility': merged.get('eligibility'),
                                     'action': merged.get('action'),
                                     'priority': bool(merged.get('priority'))}}
            if detail:
                item.update(description=row.get('description') or '', skills=list(row.get('skills') or []),
                            tags=row.get('tags'), qualification=row.get('qualification'),
                            eligibility_evidence={'rule_id': row.get('rule_id'), 'rule_kind': row.get('rule_kind'),
                                                  'matched_text': row.get('matched_text'),
                                                  'snippet': row.get('eligibility_snippet') or '',
                                                  'cue': row.get('eligibility_cue') or ''})
            items.append(item)
    result = {'plugin_id': plugin_id, 'as_of': utcnow(), 'generation': run_state.generation(config), 'items': items}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_HANDED_BYTES:
        raise APIError('response_too_large', 413)
    return result


def normalize(qs):
    allowed = set(panel_data.ALL_FILTER_KEYS) | {'priority', 'ineligible_last', 'view', 'since', 'offset', 'display'}
    if set(qs) - allowed or any(len(v) != 1 for k, v in qs.items() if k != 'skill'):
        raise APIError('invalid_query', 400)
    view = panel_data.VIEW_ALIASES.get((qs.get('view') or ['recommend'])[0], (qs.get('view') or ['recommend'])[0])
    if view not in panel_data.VIEWS:
        raise APIError('invalid_view', 400)
    try:
        filters = panel_data.parse_filters(qs)
    except (TypeError, ValueError):
        raise APIError('invalid_query', 400) from None
    # Exactly Handler.do_GET's handling, so a hand-edited URL counts the same
    # jobs in List and Map.
    if view == 'recommend':
        filters.pop('geo', None)
        filters.pop('mode', None)
    if view not in panel_data.INELIGIBLE_LAST_VIEWS:
        filters.pop(panel_data.INELIGIBLE_LAST_KEY, None)
    return view, filters


class PageSnapshots:
    def __init__(self):
        self.store = SnapshotStore(ttl=300, max_snapshots=6, max_bytes=48*1024*1024,
                                   max_snapshot_bytes=8*1024*1024, max_rows=MAX_ROWS)
        self.recent = {}   # (plugin, view, filters, generation, label watermark) -> snapshot id, oldest first

    def _reuse(self, key):
        sid = self.recent.get(key)
        if sid is None:
            return None
        try:
            item = self.store.get(sid)
        except APIError:
            self.recent.pop(key, None)
            return None
        if item.created + self.store.ttl - self.store.clock() < MIN_REUSE_TTL:
            return None     # about to expire: hand out a fresh snapshot instead
        return item

    def load(self, config, plugin_id, qs):
        """Returns (created_id_or_None, result). Re-entering the map, toggling
        List/Map or a frame reload reuses the live snapshot for the same query and
        result generation instead of exhausting the bounded store."""
        _require_list(config, plugin_id)
        view, filters = normalize(qs)
        with closing(read_connection(db_path(config))) as conn:
            label_version = labels.freeze_marker(conn).partition('|')[2]   # event id only
        key = (plugin_id, view, json.dumps(filters, sort_keys=True, default=str),
               run_state.generation(config), label_version)
        with self.store.lock:
            item = self._reuse(key)
        if item is not None:
            return None, {**item.metadata, 'items': list(item.records.values()), 'total': len(item.order)}
        created, result = self._create(config, plugin_id, view, filters, key)
        return created, result

    def _create(self, config, plugin_id, view, filters, key):
        deadline = time.monotonic() + 12

        def check():
            if time.monotonic() > deadline:
                raise APIError('snapshot_timeout', 503)

        def materialize():
            with closing(read_connection(db_path(config))) as conn:
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                cfg = skills.load_skills(config.get('skills_path'))
                since = panel_data._resolve_since(conn, view, filters)
                rows, total = panel_data._query(conn, config, view, 0, MAX_ROWS + 1,
                                                filters, since, cfg, snippets=False)
                if total > MAX_ROWS:
                    raise APIError('snapshot_too_large', 413)
                records = {}
                for order, row in enumerate(rows):
                    if order % 64 == 0:
                        check()
                    records[row['uid']] = _summary(row, order, panel_data._link(view, 0, filters, since))
                version = reference(config, plugin_id, 'index.json').get('reference_version')
                return ({'plugin_id': plugin_id, 'as_of': utcnow(), 'view': view, 'query': {'view': view, 'filters': filters},
                         'count_unit': 'source_records' if view == 'excluded' else 'jobs',
                         'generation': run_state.generation(config), 'reference_version': version},
                        [r['uid'] for r in rows], records)

        try:
            item = self.store.create(materialize, check=check)
        except APIError as exc:
            if exc.code != 'snapshot_capacity' or not self.recent:
                raise
            with self.store.lock:      # evict this page store's oldest snapshot once
                oldest = next(iter(self.recent))
                self.store.discard(self.recent.pop(oldest))
            item = self.store.create(materialize, check=check)
        with self.store.lock:
            self.recent.pop(key, None)
            self.recent[key] = item.snapshot_id
            while len(self.recent) > 6:
                self.recent.pop(next(iter(self.recent)))
        return item.snapshot_id, {**item.metadata, 'items': list(item.records.values()), 'total': len(item.order)}

    def membership(self, config, plugin_id, snapshot_id, uid):
        _require_list(config, plugin_id)
        item = self.store.get(snapshot_id)
        if item.metadata.get('plugin_id') != plugin_id or uid not in item.records:
            raise APIError('source_not_in_snapshot', 404)
        return {'ok': True, 'uid': uid}
