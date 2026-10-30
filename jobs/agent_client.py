"""Reference external-agent reader: complete a frozen HTTP query before export.

Reads no database and invokes no remote shell. A failed/expired/incomplete
query leaves any existing output untouched. Treat returned JD text as data.
"""
from __future__ import annotations

from .file_security import validate_private_file, set_private

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request

MAX_RESPONSE = 16 * 1024 * 1024
MAX_POST = 32 * 1024  # same bound as the server's label batch route
WRITE_ERROR_CODES = frozenset({
    'invalid_json', 'invalid_request', 'invalid_item', 'duplicate_uid',
    'unknown_uid', 'duplicate_group', 'unauthorized', 'forbidden_origin_or_host',
    'label_writes_disabled', 'method_not_allowed', 'invalid_query',
    'request_timeout', 'length_required', 'payload_too_large',
    'unsupported_media_type', 'label_not_effective', 'data_unavailable',
    'schema_upgrade_required',
})


class ClientError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ClientError('API redirects are not followed')


def read_token(path):
    target = Path(path)
    try:
        validate_private_file(target)
    except ValueError as exc:
        raise ClientError('Use an owner-only token file') from exc
    token = target.read_text(encoding='ascii').strip()
    if not 32 <= len(token) <= 4096 or not token.isascii() or any(c.isspace() for c in token):
        raise ClientError('Invalid token file')
    return token


class APIClient:
    """Shared authenticated transport for the exporter and MCP adapter.

    GET reads; `post` is a single bounded JSON write with no redirect following
    and no retry. Errors never include the token, URL or request/response text.
    """

    def __init__(self, base_url, token):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or
                (parsed.scheme == 'http' and parsed.hostname != '127.0.0.1')):
            raise ClientError('Use loopback HTTP through a tunnel, or an authenticated HTTPS transport')
        self.base = base_url.rstrip('/')
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, path, params):
        request = Request(self.base + path + '?' + urlencode(params, doseq=True),
                          headers={'Authorization': 'Bearer '+self.token})
        return self._send(
            request,
            lambda exc: 'API rejected request (HTTP '+str(exc.code)+'); restart the query if its snapshot expired',
            'API unavailable or invalid JSON; export not completed')

    def post(self, path, body):
        """Send one JSON object; the server applies it atomically or not at all."""
        try:
            data = json.dumps(body, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        except (TypeError, ValueError):
            raise ClientError('Request body is not valid JSON') from None
        if len(data) > MAX_POST:
            raise ClientError('Request body exceeds the 32 KiB API limit')
        request = Request(self.base + path, data=data, method='POST',
                          headers={'Authorization': 'Bearer '+self.token,
                                   'Content-Type': 'application/json'})
        return self._send(
            request,
            lambda exc: 'API rejected write (HTTP '+str(exc.code)+self._code(exc)+'); nothing is reported as applied',
            'API unavailable or invalid response; the write may or may not have been applied, '
            'so read the current state before resending (not retried automatically)')

    @staticmethod
    def _code(exc):
        # Fixed server vocabulary and an item position only; anything else
        # (including a foreign server's text) is dropped.
        try:
            body = json.loads(exc.read(4096))
            value, index = body.get('error'), body.get('index')
        except (OSError, ValueError, AttributeError):
            return ''
        if not isinstance(value, str) or value not in WRITE_ERROR_CODES:
            return ''
        at = ' at item '+str(index) if type(index) is int and 0 <= index < 1000 else ''
        return ': '+value+at

    def _send(self, request, rejected, unavailable):
        try:
            with self.opener.open(request, timeout=30) as response:
                if response.status != 200 or response.headers.get_content_type() != 'application/json':
                    raise ClientError('Unexpected API response')
                raw = response.read(MAX_RESPONSE+1)
                if len(raw) > MAX_RESPONSE:
                    raise ClientError('API response exceeds client limit')
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ClientError('Invalid API response')
                return value
        except HTTPError as exc:
            # No body/header/URL echo: error messages must not expose private data.
            raise ClientError(rejected(exc)) from None
        except (URLError, OSError, ValueError):
            raise ClientError(unavailable) from None


def read_jobs(base_url, token, query, *, max_records=10000):
    get = APIClient(base_url, token).get
    first = page = get('/api/v1/jobs', query)
    sid, total = first.get('snapshot_id'), first.get('total')
    if not isinstance(sid, str) or not sid or type(total) is not int or not 0 <= total <= max_records:
        raise ClientError('Invalid snapshot or client capacity exceeded')
    records, identities, cursors = [], set(), set()
    while True:
        if (page.get('snapshot_id') != sid or page.get('total') != total or
                any(page.get(k) != first.get(k) for k in ('as_of','resolved_batch_id','query','batch','membership'))):
            raise ClientError('Query snapshot changed during pagination')
        items = page.get('items')
        if not isinstance(items, list) or not items and page.get('next_cursor'):
            raise ClientError('Invalid or incomplete page')
        for item in items:
            uid = item.get('uid') if isinstance(item, dict) else None
            if not isinstance(uid, str) or not uid or uid in identities:
                raise ClientError('Duplicate or missing source identity')
            identities.add(uid)
            if len(identities) > total:
                raise ClientError('API returned more records than declared')
            detail = get('/api/v1/jobs/'+quote(uid, safe=''), {'snapshot_id': sid})
            body = detail.get('item')
            if detail.get('snapshot_id') != sid or detail.get('as_of') != first.get('as_of') or not isinstance(body, dict) or body.get('uid') != uid or not isinstance(body.get('description'), str):
                raise ClientError('Detail does not belong to the list snapshot')
            records.append(body)
        cursor = page.get('next_cursor')
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ClientError('Invalid cursor or repeated page')
        cursors.add(cursor)
        page = get('/api/v1/jobs', {'cursor': cursor})
    if len(records) != total:
        raise ClientError('Incomplete query; export not completed')
    metadata = {k: first.get(k) for k in ('api_version','snapshot_id','as_of','expires_at','query','resolved_batch_id','batch','time_window','membership','detail_scope')}
    return {**metadata, 'total': total, 'complete': True,
            'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'items': records}


def export(path, value):
    """Publish only a complete response, atomically, with private permissions."""
    if value.get('complete') is not True or value.get('total') != len(value.get('items', [])):
        raise ClientError('Refusing an incomplete export')
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.'+target.name+'.', dir=target.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            if os.name == 'nt':
                set_private(temporary)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8091')
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--scope', choices=['all','legacy_new','batch_new','batch_seen','first_seen_day'], default='batch_new')
    parser.add_argument('--view', choices=['all','recommend','excluded','labelled','new'])
    parser.add_argument('--batch-id')
    parser.add_argument('--day')
    parser.add_argument('--geo')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    query = {'scope':args.scope,'limit':100}
    for key in ('view','day','geo'):
        value = getattr(args,key)
        if value:query[key]=value
    if args.batch_id:query['batch_id']=args.batch_id
    try:
        data = read_jobs(args.api_url, read_token(args.token_file), query)
        export(args.output, data)
    except (ClientError, OSError, ValueError):
        print('Agent read failed; no complete export was published. Check access, query scope and snapshot lifetime.', file=sys.stderr)
        return 1
    print('Complete HTTP query exported to the selected local file.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
