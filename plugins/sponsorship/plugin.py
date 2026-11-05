"""Employer-only facts adapted from Patrickchenxi's PR #27."""
from __future__ import annotations
import re
import zipfile
import xml.etree.ElementTree as ET
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from . import reference as ref


def after_fetch(request):
    directory = Path(request['data_dir'])
    directory.mkdir(parents=True, exist_ok=True)
    days = 30
    downloads = [
        ('sanctioned.json', lambda path: ref._download(urllib.request.Request(ref.SANCTIONS_URL, data=b'{}', method='POST', headers={'Content-Type': 'application/json; charset=utf-8'}), path, ref._check_sanctions)),
        ('asx-listed.csv', lambda path: ref._download(ref.ASX_URL, path, ref._check_asx)),
    ]
    errors = []
    for name, download in downloads:
        try:
            if ref._stale(directory / name, days):
                download(directory / name)
        except (OSError, ValueError):
            errors.append(name)
    try:
        existing = sorted(directory.glob('ato-tax-*.xlsx'))
        damaged = False
        if existing:
            try:
                ref.load_ato(directory)
            except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError):
                damaged = True
        if not existing or damaged or ref._stale(existing[-1], days):
            latest = ref._latest_ato_url()
            if latest is None:
                raise ValueError('ATO reference unavailable')
            ref._download(latest[1], directory / ('ato-tax-' + latest[0] + '.xlsx'), ref._check_ato)
    except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile, ET.ParseError):
        errors.append('ato')
    return {'errors': errors}


def _date(path):
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).date().isoformat()
    except OSError:
        return 'unknown'


def after_analyze_success(request):
    directory = Path(request['data_dir'])
    accredited = ref.NameIndex(ref.load_accredited(directory))
    sanctions = ref.load_sanctions(directory)
    companies = ref.Companies(directory, ref.NameIndex)
    accredited_files = sorted(directory.glob('accredited-*.txt'))
    acc_source = ', '.join(p.name for p in accredited_files) or 'unavailable'
    asx_date = _date(directory / 'asx-listed.csv')
    abf_date = _date(directory / 'sanctioned.json')
    results = {}
    for job in request['jobs']:
        company = job.get('company') or ''
        views = {lang: {'badges': [], 'sections': []} for lang in ('zh', 'en')}
        if not company.strip() or ref._AGENCY.search(company) or re.search(r'\b(anonymous|confidential|undisclosed)\b', company, re.I):
            for lang, text in [('zh', '招聘机构或匿名雇主，实际雇主未知'), ('en', 'Agency or anonymous advertiser; employer unknown')]:
                views[lang]['sections'].append({'title': '雇主信息' if lang == 'zh' else 'Employer facts', 'status': '未知' if lang == 'zh' else 'Unknown', 'items': [text]})
        else:
            acc, acc_name = accredited.match(company)
            sanction, sanction_row = sanctions.match(company)
            size = companies.lookup(company)
            for lang in ('zh', 'en'):
                items = []
                if acc:
                    label = ('认证担保名单匹配' if acc == 'exact' else '可能匹配认证担保名单') if lang == 'zh' else ('Accredited sponsor match' if acc == 'exact' else 'Possible accredited sponsor match')
                    items.append(f'{label}: {acc_name} (Home Affairs FOI, {acc_source})')
                    views[lang]['badges'].append({'text': label, 'level': 'info', 'tooltip': items[-1]})
                if sanction:
                    label = ('违规名单确认匹配' if sanction == 'confirmed' else '可能关联违规名单') if lang == 'zh' else ('Confirmed sanctions match' if sanction == 'confirmed' else 'Possible sanctions match')
                    items.append(f"{label}: {sanction_row.get('name', '')} (ABF, {_date(directory / 'sanctioned.json')}, {sanction_row.get('date') or 'unknown'})")
                    views[lang]['badges'].append({'text': label, 'level': 'warn', 'tooltip': items[-1]})
                if size['asx']:
                    items.append(f"{'ASX 上市名单' if lang == 'zh' else 'ASX listed companies'} ({size['asx']}): {size['asx_name']} (ASX, {asx_date})")
                if size['ato']:
                    items.append(f"{'ATO 企业税务透明度名单' if lang == 'zh' else 'ATO corporate tax transparency'} ({size['ato']}): {size['ato_name']} (ATO, {size['ato_year'] or 'unknown'})")
                if not acc:
                    items.append((('认证担保名单未匹配' if accredited_files else '认证担保名单未导入') + f' (Home Affairs FOI, {acc_source})') if lang == 'zh' else (('No accredited-list match' if accredited_files else 'Accredited list not imported') + f' (Home Affairs FOI, {acc_source})'))
                if not sanction:
                    items.append((('违规名单未匹配' if (directory / 'sanctioned.json').exists() else '违规名单不可用') + f' (ABF, {abf_date})') if lang == 'zh' else (('No sanctions-list match' if (directory / 'sanctioned.json').exists() else 'Sanctions list unavailable') + f' (ABF, {abf_date})'))
                if not size['asx'] and not (directory / 'asx-listed.csv').exists():
                    items.append('ASX 名单不可用' if lang == 'zh' else 'ASX list unavailable')
                if not size['ato'] and not list(directory.glob('ato-tax-*.xlsx')):
                    items.append('ATO 名单不可用' if lang == 'zh' else 'ATO list unavailable')
                items.append('名单记录不保证当前岗位提供担保；未匹配也不表示企业不能担保。' if lang == 'zh' else 'A list entry does not guarantee sponsorship for this job; no match does not establish ineligibility.')
                if items:
                    views[lang]['sections'].append({'title': '雇主信息' if lang == 'zh' else 'Employer facts', 'status': '参考' if lang == 'zh' else 'Reference', 'items': items})
        results[job['uid']] = views
    return {'analysis_id': request['analysis_id'], 'results': results}
