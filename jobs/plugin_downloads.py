"""Host-owned public sponsorship downloads. No plugin-selected URLs or bodies."""
from __future__ import annotations
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from . import RunTimeout

ABF = 'https://www.abf.gov.au/_layouts/15/api/Data.aspx/GetSanctionsData'
ASX = 'https://www.asx.com.au/asx/research/ASXListedCompanies.csv'
ATO = 'https://data.gov.au/data/api/3/action/package_show?id=corporate-transparency'
LIMIT = 64 * 1024 * 1024
_ATO_PATH = re.compile(r'^/data/dataset/c2524c87-cea4-4636-acac-599a82048a26/resource/[a-f0-9-]+/download/[a-zA-Z0-9._-]+\.xlsx$')


def _allowed(url, kind):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.fragment:
        return False
    if kind == 'ato_file':
        return p.hostname == 'data.gov.au' and not p.query and bool(_ATO_PATH.fullmatch(p.path))
    return url == {'abf': ABF, 'asx': ASX, 'ato': ATO}[kind]


class _Redirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind):
        self.kind = kind
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl, self.kind):
            raise ValueError('unapproved redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(kind, url):
    if not _allowed(url, kind):
        raise ValueError('unapproved target')
    body = b'{}' if kind == 'abf' else None
    request = urllib.request.Request(url, data=body, headers={'User-Agent': 'au-job-board (public sponsorship references)',
        **({'Content-Type': 'application/json; charset=utf-8'} if body else {})})
    opener = urllib.request.build_opener(_Redirect(kind), urllib.request.ProxyHandler({}))
    limit = 2 * 1024 * 1024 if kind == 'ato' else LIMIT
    with opener.open(request, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('reference too large')
    return data


def refresh(directory):
    # Host validators never import installed plugin code.
    from . import plugin_reference_validate as ref
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    errors = []
    def update(kind, url, name, validator):
        target = directory / name
        if not ref._stale(target, 30):
            try:
                if target.stat().st_size > LIMIT:
                    raise ValueError('reference too large')
                validator(target.read_bytes())
                return
            except Exception as exc:
                if isinstance(exc, RunTimeout):
                    raise
        data = _read(kind, url)
        validator(data)
        tmp = target.with_name(target.name + '.tmp')
        try:
            tmp.write_bytes(data)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    for kind, url, name, check in [('abf', ABF, 'sanctioned.json', ref._check_sanctions),
                                    ('asx', ASX, 'asx-listed.csv', ref._check_asx)]:
        try:
            update(kind, url, name, check)
        except RunTimeout:
            raise
        except Exception:
            errors.append(kind)
    try:
        existing = sorted(directory.glob('ato-tax-*.xlsx'))
        damaged = False
        if existing:
            try:
                if existing[-1].stat().st_size > LIMIT:
                    raise ValueError('reference too large')
                ref._check_ato(existing[-1].read_bytes())
            except RunTimeout:
                raise
            except Exception:
                damaged = True
        if not existing or damaged or ref._stale(existing[-1], 30):
            package = json.loads(_read('ato', ATO))['result']
            reports = []
            for resource in package.get('resources') or []:
                year = re.match(r'^(\d{4}-\d{2}) Report of Entity Tax Information$', resource.get('name') or '')
                url = resource.get('url') or ''
                if year and _allowed(url, 'ato_file'):
                    reports.append((year.group(1), url))
            if not reports:
                raise ValueError('no ATO report')
            year, url = max(reports)
            update('ato_file', url, 'ato-tax-' + year + '.xlsx', ref._check_ato)
    except RunTimeout:
        raise
    except Exception:
        errors.append('ato')
    return errors


if __name__ == '__main__':
    # Trusted host code, supervised by the same process/time/resource boundary
    # as plugin hooks. Never import an installed plugin in this process.
    from .plugin_worker import _exit_with_parent, _resource_limits
    _exit_with_parent()
    _resource_limits()
    input_path, output_path = map(Path, sys.argv[1:])
    request = json.loads(input_path.read_text(encoding='utf-8'))
    errors = refresh(request['data_dir'])
    output_path.write_text(json.dumps({'errors': errors}), encoding='utf-8')
