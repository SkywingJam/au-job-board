"""Authenticated loopback HTTP reads for external agents; no writer is mounted.

Run ``python -m jobs.agent_api --help``. The normal application must upgrade the
DB first. This process only opens existing databases with mode=ro/query_only.
"""
from __future__ import annotations

from .file_security import validate_private_file

import argparse
import base64
import copy
from contextlib import closing
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from .http_server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from . import config as config_mod, labels, location_query
from . import locations, migrations, panel_data, report, run_state, skills

UTC = dt.timezone.utc
DAY_ZONE = 'Australia/Melbourne'
PREFIX = '/api/v1'


class APIError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def utcnow():
    return dt.datetime.now(UTC).isoformat(timespec='seconds')


def read_connection(path):
    """Never create directories, change journal mode, migrate or repair state."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        if conn.execute('PRAGMA user_version').fetchone()[0] != migrations.SCHEMA_VERSION:
            raise APIError('schema_upgrade_required', 503)
        return conn
    except BaseException:
        conn.close()
        raise


def day_window(value):
    try:
        date = dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise APIError('invalid_day') from None
    if value != date.isoformat():
        raise APIError('invalid_day')
    zone = ZoneInfo(DAY_ZONE)
    try:
        return tuple(dt.datetime.combine(d, dt.time.min, zone).astimezone(UTC)
                     for d in (date, date + dt.timedelta(days=1)))
    except OverflowError:
        raise APIError('invalid_day') from None


def _timestamp(value):
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(UTC) if stamp.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _integer(value, low, high):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise APIError('invalid_pagination') from None
    if not low <= result <= high:
        raise APIError('invalid_pagination')
    return result


def normalize_query(qs):
    allowed = set(panel_data.ALL_FILTER_KEYS) | {'priority', panel_data.INELIGIBLE_LAST_KEY, 'view', 'scope', 'day', 'batch_id', 'limit'}
    if set(qs) - allowed or any(len(v) != 1 for k, v in qs.items() if k != 'skill'):
        raise APIError('invalid_query')
    one = lambda key, default='': (qs.get(key) or [default])[0]
    view = one('view', 'all')
    scope = one('scope', 'legacy_new' if view == 'new' else 'all')
    if view not in panel_data.VIEW_FILTER_KEYS or scope not in ('all', 'legacy_new', 'first_seen_day', 'batch_new', 'batch_seen'):
        raise APIError('invalid_scope')
    if scope == 'legacy_new':
        if 'view' in qs and view != 'new':
            raise APIError('invalid_scope')
        view = 'new'
    elif view == 'new':
        raise APIError('invalid_scope')
    if ('day' in qs) != (scope == 'first_seen_day'):
        raise APIError('day_required_only_for_day_scope')
    if scope == 'first_seen_day':
        day_window(one('day'))
    if 'batch_id' in qs and scope not in ('batch_new', 'batch_seen'):
        raise APIError('batch_not_supported_for_scope')
    if one('batch_id') == '' and 'batch_id' in qs:
        raise APIError('invalid_batch')
    limit = _integer(one('limit', '100'), 1, 200)
    for key in ('salary', 'fresh', 'priority', panel_data.INELIGIBLE_LAST_KEY):
        if key in qs and one(key) != '1':
            raise APIError('invalid_filter')
    if len(qs.get('skill', [])) > 20 or any(not s for s in qs.get('skill', [])):
        raise APIError('invalid_filter')
    for key in ('q', 'geo'):
        if len(one(key)) > 120:
            raise APIError('invalid_filter')
    for key in panel_data.FILTER_KEYS:
        if key in qs and one(key) not in set(labels.VALID[key]) | {panel_data.NONE_TOKEN}:
            raise APIError('invalid_filter')
    if 'tier' in qs and one('tier') not in panel_data.TIER_LABELS:
        raise APIError('invalid_filter')
    if 'sort' in qs and one('sort') not in panel_data.SORT_KEYS:
        raise APIError('invalid_filter')
    if 'labeled' in qs and one('labeled') not in ('yes', 'no', panel_data.LABELED_ANY):
        raise APIError('invalid_filter')
    try:
        filters = panel_data.parse_filters(qs)
    except ValueError:
        raise APIError('invalid_filter') from None
    if set(filters) - set(panel_data.VIEW_FILTER_KEYS[view]):
        raise APIError('filter_not_supported_for_view')
    return {'view': view, 'scope': scope, 'day': one('day') or None,
            'batch_id': one('batch_id') or None, 'filters': filters, 'limit': limit}


def _panel_base(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('panel URL must be an HTTP(S) base without credentials/query/fragment')
    return value.rstrip('/') + '/'


def panel_link(base, uid, filters=None):
    params = {'view': 'all'}
    params.update({k: v for k, v in (filters or {}).items() if k == 'geo'})
    suffix = quote(uid, safe='')
    return base + '?' + urlencode(params) + '#' + suffix


def _human(value):
    keys = ('eligibility', 'interest', 'action', 'priority', 'note', 'labeled_at',
            'action_at', 'applied_at', 'eligibility_source', 'eligibility_at', 'eligibility_analysis')
    return {k: value.get(k) for k in keys}


def serialize(conn, row, panel_base, filters):
    keys = ('uid', 'source', 'title', 'company', 'location', 'url', 'salary', 'work_type',
            'listing_date', 'first_seen_at', 'last_seen_at', 'dedupe_group', 'tier', 'total_score',
            'skills', 'tags', 'is_excluded', 'rule_id', 'rule_kind', 'matched_text', 'reasons',
            'decision_rules_version', 'decided_at', 'eligibility_snippet', 'eligibility_cue', 'qualification')
    out = {k: row.get(k) for k in keys}
    out.update(description=row.get('description') or '', description_available=bool(row.get('description')),
               source_id=row['uid'].partition(':')[2],
               location_match=row.get('_geo_match'),
               human_scope='group',
               human=_human(row.get('_label') or {}),
               panel_url=panel_link(panel_base, row['uid'], filters))
    return out


def _pipeline(config, task_id):
    # Reading latest() could recover and publish state. Read only bounded JSON.
    unknown = {'state': 'unknown', 'task_id': task_id, 'reason': 'no_confirmed_terminal_evidence'}
    if not task_id:
        return unknown
    root = run_state.runtime_path(config)
    try:
        raw = run_state.read_shared_bytes(root / 'latest.json')
        if len(raw) > 65536:
            return unknown
        value = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return unknown
    if not isinstance(value, dict) or value.get('task_id') != task_id:
        return unknown
    state = value.get('state')
    if (state not in ('success', 'partial', 'error', 'interrupted', 'timeout') or
            value.get('completion_ready') is not True):
        return unknown
    if value.get('refresh_available'):
        try:
            raw = run_state.read_shared_bytes(root / 'generation.json')
            if len(raw) > 65536:
                return unknown
            generation = json.loads(raw)
        except (OSError, ValueError, TypeError):
            return unknown
        if not isinstance(generation, dict) or generation.get('generation') != task_id:
            return unknown
    return {k: value.get(k) for k in ('task_id', 'state', 'started_at', 'finished_at', 'stage', 'error')}


def batch_payload(conn, config, batch):
    keys = ('batch_id', 'started_at', 'finished_at', 'collection_state', 'membership', 'counts')
    out = {k: batch.get(k) for k in keys}
    out['pipeline'] = _pipeline(config, batch.get('pipeline_task_id'))
    return out


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    created: float
    metadata: dict
    order: tuple
    records: dict
    size: int


class SnapshotStore:
    def __init__(self, *, ttl=300, max_snapshots=8, max_bytes=512*1024*1024,
                 max_snapshot_bytes=128*1024*1024, max_rows=50000, clock=time.monotonic):
        if min(ttl, max_snapshots, max_bytes, max_snapshot_bytes, max_rows) <= 0:
            raise ValueError('snapshot limits must be positive')
        self.ttl, self.max_snapshots, self.max_bytes = ttl, max_snapshots, max_bytes
        self.max_snapshot_bytes, self.max_rows = max_snapshot_bytes, max_rows
        self.clock, self.lock = clock, threading.RLock()
        self.items, self.secret = {}, secrets.token_bytes(32)

    def _expire(self):
        for key, item in list(self.items.items()):
            if self.clock() - item.created >= self.ttl:
                del self.items[key]

    def create(self, loader):
        with self.lock:
            self._expire()
            if len(self.items) >= self.max_snapshots:
                raise APIError('snapshot_capacity', 429)
            metadata, order, records = loader()
            size = len(json.dumps([metadata, order, records], ensure_ascii=False, allow_nan=False).encode())
            if len(order) > self.max_rows or size > self.max_snapshot_bytes:
                raise APIError('snapshot_too_large', 413)
            if size + sum(item.size for item in self.items.values()) > self.max_bytes:
                raise APIError('snapshot_capacity', 429)
            sid = secrets.token_urlsafe(24)
            metadata.update(snapshot_id=sid, expires_at=(dt.datetime.now(UTC) + dt.timedelta(seconds=self.ttl)).isoformat(timespec='seconds'))
            item = Snapshot(sid, self.clock(), metadata, tuple(order), records, size)
            self.items[sid] = item
            return item

    def get(self, sid):
        with self.lock:
            self._expire()
            if sid not in self.items:
                raise APIError('snapshot_expired_or_unknown', 410)
            return self.items[sid]

    def cursor(self, item, offset):
        raw = json.dumps([item.snapshot_id, offset, item.metadata['query']['limit']], separators=(',', ':')).encode()
        signed = raw + b'.' + hmac.digest(self.secret, raw, 'sha256').hex().encode()
        return base64.urlsafe_b64encode(signed).decode().rstrip('=')

    def resume(self, cursor):
        try:
            raw, signature = base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)).rsplit(b'.', 1)
            if not hmac.compare_digest(signature, hmac.digest(self.secret, raw, 'sha256').hex().encode()):
                raise ValueError()
            sid, offset, limit = json.loads(raw)
            if type(offset) is not int or offset < 1 or type(limit) is not int:
                raise ValueError()
        except (ValueError, TypeError, UnicodeError, KeyError):
            raise APIError('invalid_cursor') from None
        item = self.get(sid)
        if limit != item.metadata['query']['limit'] or offset >= len(item.order) or offset % limit:
            raise APIError('invalid_cursor')
        return item, offset

    def page(self, item, offset=0):
        limit = item.metadata['query']['limit']
        ids = item.order[offset:offset+limit]
        # Return fresh objects; response assembly cannot mutate the cache.
        rows = [{k: v for k, v in item.records[uid].items() if k != 'description'} for uid in ids]
        result = {**item.metadata, 'total': len(item.order), 'items': rows,
                  'next_cursor': self.cursor(item, offset+limit) if offset+limit < len(item.order) else None}
        return copy.deepcopy(result)


def snapshot_store(config):
    """One validated capacity configuration shared by HTTP and MCP readers."""
    section = config.get('agent_api', {})
    if not isinstance(section, dict):
        raise ValueError('agent_api must be an object')
    values = section.get('snapshots', {})
    defaults = {'ttl_seconds': 300, 'max_snapshots': 8, 'max_rows': 50000,
                'max_snapshot_mib': 128, 'max_total_mib': 512}
    if not isinstance(values, dict) or set(values) - defaults.keys():
        raise ValueError('Invalid agent_api.snapshots settings')
    merged = {**defaults, **values}
    if any(type(value) is not int or value <= 0 for value in merged.values()):
        raise ValueError('Snapshot settings must be positive integers')
    if merged['max_snapshot_mib'] > merged['max_total_mib']:
        raise ValueError('Snapshot budget exceeds total budget')
    return SnapshotStore(ttl=merged['ttl_seconds'], max_snapshots=merged['max_snapshots'],
        max_rows=merged['max_rows'], max_snapshot_bytes=merged['max_snapshot_mib']*1024*1024,
        max_bytes=merged['max_total_mib']*1024*1024)


class AgentAPI:
    def __init__(self, config, *, panel_url='http://127.0.0.1:8090/', store=None):
        self.config = copy.deepcopy(config)
        self.db_path = config_mod.db_path(config)
        self.panel_base = _panel_base(panel_url)
        self.store = store if store is not None else snapshot_store(config)

    def _candidates(self, conn, query):
        scope, batch = query['scope'], None
        if scope == 'all' or scope == 'legacy_new':
            return None, None, None
        if scope in ('batch_new', 'batch_seen'):
            from . import batches
            requested = query['batch_id']
            available = batches.list_batches(conn, limit=1, offset=0) if requested is None else []
            batch = batches.get_batch(conn, requested) if requested else next(iter(available), None)
            if batch is None:
                raise APIError('batch_not_found', 404)
            if batch['membership'] != 'available':
                raise APIError('batch_membership_unavailable', 409)
            if batch['collection_state'] == 'running':
                raise APIError('batch_not_finalized', 409)
            sql = 'SELECT uid FROM fetch_batch_members WHERE batch_id=?'
            if scope == 'batch_new':
                sql += ' AND is_new=1'
            return {r[0] for r in conn.execute(sql, (batch['batch_id'],))}, batch, None
        start, end = day_window(query['day'])
        window = {'start': start.isoformat(), 'end': end.isoformat(), 'timezone': DAY_ZONE}
        uids = {r['uid'] for r in conn.execute('SELECT uid,first_seen_at FROM jobs')
                if (stamp := _timestamp(r['first_seen_at'])) is not None and start <= stamp < end}
        return uids, None, window

    def _materialize(self, query):
        with closing(read_connection(self.db_path)) as conn:
            try:
                count, body_bytes = conn.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(CAST(description AS BLOB))),0) FROM jobs').fetchone()
                if count > self.store.max_rows or body_bytes > self.store.max_snapshot_bytes:
                    raise APIError('snapshot_too_large', 413)
                candidates, batch, window = self._candidates(conn, query)
                filters = query['filters']
                view = query['view']
                cfg = skills.load_skills(self.config.get('skills_path'))
                rows, _ = panel_data._query(conn, self.config, view, 0, self.store.max_rows + 1,
                                             filters, skills_cfg=cfg, candidate_uids=candidates)
                kept, excluded, legacy, index = report.load(conn, self.config, deduplicate=False)
                pool = [r for r in kept+excluded if candidates is None or r['uid'] in candidates]
                records = {}
                for row in pool:
                    target = locations.parse_target(filters['geo']) if filters.get('geo') else None
                    decorated = location_query.decorate(conn, row, legacy, index, target=target)
                    records[row['uid']] = serialize(conn, decorated, self.panel_base, filters)
                for row in rows:
                    records[row['uid']] = serialize(conn, row, self.panel_base, filters)
                if batch:
                    members = {r['uid']: dict(r) for r in conn.execute('SELECT uid,is_new,seen_at FROM fetch_batch_members WHERE batch_id=?', (batch['batch_id'],))}
                    for uid, record in records.items():
                        member = members.get(uid)
                        record['batch_membership'] = {'batch_id': batch['batch_id'], 'is_new': bool(member['is_new']), 'seen_at': member['seen_at']} if member else None
                metadata = {'api_version': 1, 'as_of': utcnow(), 'query': query,
                            'resolved_batch_id': batch['batch_id'] if batch else None,
                            'batch': batch_payload(conn, self.config, batch) if batch else None,
                            'time_window': window,
                            'membership': 'legacy_approximate' if query['scope'] == 'legacy_new' else 'available',
                            'detail_scope': 'source_candidates_before_display_deduplication'}
                return metadata, [r['uid'] for r in rows], records
            finally:
                conn.close()

    def jobs(self, qs):
        if 'cursor' in qs:
            if set(qs) != {'cursor'} or len(qs['cursor']) != 1 or not qs['cursor'][0]:
                raise APIError('cursor_query_mismatch')
            item, offset = self.store.resume(qs['cursor'][0])
            return self.store.page(item, offset)
        query = normalize_query(qs)
        return self.store.page(self.store.create(lambda: self._materialize(query)))

    def detail(self, uid, qs):
        if set(qs) - {'snapshot_id'} or any(len(v) != 1 for v in qs.values()):
            raise APIError('invalid_query')
        sid = (qs.get('snapshot_id') or [None])[0]
        if 'snapshot_id' in qs and not sid:
            raise APIError('invalid_snapshot')
        if sid:
            item = self.store.get(sid)
            if uid not in item.records:
                raise APIError('source_not_in_snapshot', 404)
            result = copy.deepcopy(item.records[uid])
            as_of = item.metadata['as_of']
        else:
            with closing(read_connection(self.db_path)) as conn:
                try:
                    row = location_query.find(conn, self.config, uid)
                    if row is None:
                        raise APIError('job_not_found', 404)
                    result = serialize(conn, row, self.panel_base, {})
                    as_of = utcnow()
                finally:
                    conn.close()
        return {'api_version': 1, 'as_of': as_of, 'snapshot_id': sid, 'item': result}

    def batches(self, batch_id, qs):
        from . import batches
        with closing(read_connection(self.db_path)) as conn:
            try:
                if batch_id:
                    if qs:
                        raise APIError('invalid_query')
                    batch = batches.get_batch(conn, batch_id)
                    if not batch:
                        raise APIError('batch_not_found', 404)
                    return {'api_version': 1, 'as_of': utcnow(), 'item': batch_payload(conn, self.config, batch)}
                if set(qs) - {'limit', 'offset'} or any(len(v) != 1 for v in qs.values()):
                    raise APIError('invalid_query')
                limit = _integer((qs.get('limit') or ['100'])[0], 1, 200)
                offset = _integer((qs.get('offset') or ['0'])[0], 0, 1000000)
                rows = batches.list_batches(conn, limit=limit+1, offset=offset)
                return {'api_version': 1, 'as_of': utcnow(), 'items': [batch_payload(conn, self.config, b) for b in rows[:limit]],
                        'next_offset': offset+limit if len(rows) > limit else None}
            finally:
                conn.close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'jobs-readonly'
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass  # Never log headers, query values, job content or credentials.

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _authorized(self):
        auth = self.headers.get_all('Authorization') or []
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), ('Bearer '+self.server.token).encode()):
            self._json({'error': 'unauthorized'}, 401)
            return False
        expected = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get_all('Host') != [expected] or self.headers.get('Origin') is not None:
            self._json({'error': 'forbidden_origin_or_host'}, 403)
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        try:
            if len(self.path) > 4096:
                raise APIError('query_too_large', 414)
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment:
                raise APIError('invalid_request')
            qs = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=40, errors='strict')
            if parsed.path == PREFIX+'/jobs':
                result = self.server.api.jobs(qs)
            elif parsed.path.startswith(PREFIX+'/jobs/'):
                uid = unquote(parsed.path[len(PREFIX+'/jobs/'):], errors='strict')
                if not uid or len(uid) > 512:
                    raise APIError('invalid_uid')
                result = self.server.api.detail(uid, qs)
            elif parsed.path == PREFIX+'/batches':
                result = self.server.api.batches(None, qs)
            elif parsed.path.startswith(PREFIX+'/batches/'):
                bid = unquote(parsed.path[len(PREFIX+'/batches/'):], errors='strict')
                if not bid or len(bid) > 128:
                    raise APIError('invalid_batch')
                result = self.server.api.batches(bid, qs)
            else:
                raise APIError('not_found', 404)
            self._json(result)
        except APIError as exc:
            self._json({'error': exc.code}, exc.status)
        except (ValueError, UnicodeError):
            self._json({'error': 'invalid_query'}, 400)
        except (sqlite3.Error, OSError, KeyError, TypeError):
            self._json({'error': 'data_unavailable'}, 503)

    def _not_allowed(self):
        if self._authorized():
            self._json({'error': 'method_not_allowed'}, 405)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = do_CONNECT = _not_allowed


def make_server(config, token, *, host='127.0.0.1', port=8091, panel_url='http://127.0.0.1:8090/', store=None):
    # Docker publishes on host loopback. The wildcard is only a bind address
    # inside the container; it never becomes an accepted HTTP Host authority.
    if host not in ('127.0.0.1', 'localhost') and not (
            host == '0.0.0.0' and os.environ.get('JOBS_CONTAINER') == '1'):
        raise ValueError('API host must be loopback; 0.0.0.0 requires JOBS_CONTAINER=1')
    if not isinstance(token, str) or not 32 <= len(token) <= 4096 or any(c.isspace() for c in token) or not token.isascii():
        raise ValueError('token must be 32 to 4096 non-whitespace ASCII characters')
    api = AgentAPI(config, panel_url=panel_url, store=store)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.token = token
    server.api = api
    return server


def read_token(path):
    target = Path(path)
    try:
        validate_private_file(target)
    except ValueError as exc:
        raise ValueError('token file must be a small regular file accessible only to its owner') from exc
    return target.read_text(encoding='ascii').strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--rules')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--host', default='127.0.0.1',
                        help='IPv4 loopback by default; 0.0.0.0 requires JOBS_CONTAINER=1')
    parser.add_argument('--port', type=int, default=8091)
    parser.add_argument('--panel-url', default='http://127.0.0.1:8090/')
    args = parser.parse_args(argv)
    try:
        config = config_mod.load_config(args.config)
        config['rules_path'] = str(Path(args.rules).resolve() if args.rules
                                   else config_mod.ROOT / 'rules.yaml')
        token = read_token(args.token_file)
        # Fail before listening if the installation is missing/outdated.
        conn = read_connection(config_mod.db_path(config)); conn.close()
        server = make_server(config, token, host=args.host, port=args.port, panel_url=args.panel_url)
    except (OSError, ValueError, APIError, sqlite3.Error):
        parser.exit(2, 'Cannot start read-only API: check host (wildcard requires JOBS_CONTAINER=1), '
                    'config, owner-only token file and upgraded database.\n')
    print(f'Read-only API listening on {args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
