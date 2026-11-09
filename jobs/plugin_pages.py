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

from . import RunTimeout, labels, locations, panel_data, plugin_host, run_state, skills
from .agent_api import APIError, SnapshotStore, read_connection, utcnow
from .config import db_path

MAX_ROWS = 20000
MAX_ASSET = 8 * 1024 * 1024
MAX_REFERENCE = 12 * 1024 * 1024
MIN_REUSE_TTL = 120     # seconds a reused snapshot must still have left


def require_page(config, plugin_id):
    page = next((p for p in plugin_host.page_plugins(config) if p['id'] == plugin_id), None)
    if page is None:
        raise APIError('plugin_disabled_or_permission_required', 403)
    return page


def resource(config, plugin_id, relative):
    """Regular declared web files only, with no symlink/path escape."""
    require_page(config, plugin_id)
    if (not isinstance(relative, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]+', relative)
            or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise APIError('invalid_asset', 404)
    base = plugin_host.PLUGIN_ROOT / plugin_id / 'web'
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
    require_page(config, plugin_id)
    base = plugin_host.PLUGIN_ROOT / plugin_id / 'reference'
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
        require_page(config, plugin_id)
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
                    uid = row['uid']
                    records[uid] = {
                        'uid': uid, 'group_id': row.get('dedupe_group') or uid,
                        'title': row.get('title') or '', 'company': row.get('company') or '',
                        'location_raw': row.get('location') or '',
                        'location_fact': locations.parse_listing(row.get('location')),
                        'salary': row.get('salary') or '', 'work_type': row.get('work_type') or '',
                        'listing_date': row.get('listing_date'), 'total_score': row.get('total_score'),
                        'priority': bool((row.get('_label') or {}).get('priority')),
                        'core_order': order,
                        'panel_url': panel_data._link(view, 0, filters, since) + '#' + quote(uid, safe=''),
                    }
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
        require_page(config, plugin_id)
        item = self.store.get(snapshot_id)
        if item.metadata.get('plugin_id') != plugin_id or uid not in item.records:
            raise APIError('source_not_in_snapshot', 404)
        return {'ok': True, 'uid': uid}
