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


def _summary(lang, acc, sanction, acc_available, abf_available):
    if sanction:
        text = ('违规名单确认匹配' if sanction == 'confirmed' else '可能关联违规名单') if lang == 'zh' else ('Confirmed sanctions match' if sanction == 'confirmed' else 'Possible sanctions match')
        if acc:
            text += (' · 认证名单匹配' if acc == 'exact' else ' · 认证名单可能匹配') if lang == 'zh' else (' · Accredited list match' if acc == 'exact' else ' · Possible accredited list match')
        return text
    if acc:
        return ('认证担保名单匹配' if acc == 'exact' else '认证名单可能匹配') if lang == 'zh' else ('Accredited list match' if acc == 'exact' else 'Possible accredited list match')
    if not acc_available and not abf_available:
        return '暂无可用参考名单' if lang == 'zh' else 'Reference lists unavailable'
    if not acc_available:
        return '认证名单未导入' if lang == 'zh' else 'Accredited list not imported'
    if not abf_available:
        return '违规名单不可用' if lang == 'zh' else 'Sanctions list unavailable'
    return '参考名单均未匹配' if lang == 'zh' else 'No reference-list match'


def after_analyze_success(request):
    directory = Path(request['data_dir'])
    accredited = ref.NameIndex(ref.load_accredited(directory))
    sanctions = ref.load_sanctions(directory)
    companies = ref.Companies(directory, ref.NameIndex)
    accredited_files = sorted(directory.glob('accredited-*.txt'))
    acc_source = ', '.join(p.name for p in accredited_files) or 'unavailable'
    asx_date = _date(directory / 'asx-listed.csv')
    abf_date = _date(directory / 'sanctioned.json')
    abf_available = (directory / 'sanctioned.json').exists()
    results = {}
    for job in request['jobs']:
        company = job.get('company') or ''
        views = {lang: {'badges': [], 'sections': []} for lang in ('zh', 'en')}
        if not company.strip() or ref._AGENCY.search(company) or re.search(r'\b(anonymous|confidential|undisclosed)\b', company, re.I):
            for lang in ('zh', 'en'):
                views[lang]['sections'].append({
                    'title': '雇主信息' if lang == 'zh' else 'Employer facts',
                    'status': '实际雇主未知' if lang == 'zh' else 'Actual employer unknown',
                    'items': ['招聘机构或匿名发布，未查询实际雇主名单。' if lang == 'zh' else 'Agency or anonymous advertiser; actual employer lists were not checked.']})
        else:
            acc, acc_name = accredited.match(company)
            sanction, sanction_row = sanctions.match(company)
            size = companies.lookup(company)
            for lang in ('zh', 'en'):
                zh = lang == 'zh'
                items = []
                if acc:
                    label = ('认证担保名单匹配' if acc == 'exact' else '认证名单可能匹配') if zh else ('Accredited list match' if acc == 'exact' else 'Possible accredited list match')
                    evidence = (f'认证担保名单：{"匹配" if acc == "exact" else "名称相近，可能匹配"} {acc_name} · 来源 Home Affairs FOI（{acc_source}）' if zh else f'Accredited list: {"match" if acc == "exact" else "possible match, similar name"}, {acc_name} · Source: Home Affairs FOI ({acc_source})')
                    items.append(evidence)
                    views[lang]['badges'].append({'text': label, 'level': 'info', 'tooltip': evidence})
                elif accredited_files:
                    items.append(f'认证担保名单：未匹配 · 来源 Home Affairs FOI（{acc_source}）' if zh else f'Accredited list: no match · Source: Home Affairs FOI ({acc_source})')
                if sanction:
                    label = ('违规名单确认匹配' if sanction == 'confirmed' else '可能关联违规名单') if zh else ('Confirmed sanctions match' if sanction == 'confirmed' else 'Possible sanctions match')
                    date = sanction_row.get('date') or ('未知' if zh else 'unknown')
                    evidence = (f'违规名单：{"确认匹配" if sanction == "confirmed" else "可能关联"} {sanction_row.get("name", "")} · 来源 ABF · 名单下载 {abf_date} · 记录日期 {date}' if zh else f'Sanctions: {"confirmed match" if sanction == "confirmed" else "possible match"}, {sanction_row.get("name", "")} · Source: ABF · Downloaded {abf_date} · Record date {date}')
                    items.append(evidence)
                    views[lang]['badges'].append({'text': label, 'level': 'warn', 'tooltip': evidence})
                elif abf_available:
                    items.append(f'违规名单：未匹配 · 来源 ABF · 名单下载 {abf_date}' if zh else f'Sanctions: no match · Source: ABF · Downloaded {abf_date}')
                if size['asx']:
                    match = ('匹配' if size['asx'] == 'exact' else '可能匹配') if zh else ('match' if size['asx'] == 'exact' else 'possible match')
                    items.append(f'ASX 上市名单：{match} {size["asx_name"]} · 名单下载 {asx_date}' if zh else f'ASX listed companies: {match}, {size["asx_name"]} · Downloaded {asx_date}')
                if size['ato']:
                    match = ('匹配' if size['ato'] == 'exact' else '可能匹配') if zh else ('match' if size['ato'] == 'exact' else 'possible match')
                    year = size['ato_year'] or ('未知' if zh else 'unknown')
                    items.append(f'ATO 企业税务透明度名单：{match} {size["ato_name"]} · 报告年度 {year}' if zh else f'ATO corporate tax transparency: {match}, {size["ato_name"]} · Reporting year {year}')
                missing = []
                if not accredited_files:
                    missing.append('认证担保名单未导入' if zh else 'accredited list not imported')
                if not abf_available:
                    missing.append('ABF 名单不可用' if zh else 'ABF list unavailable')
                if not (directory / 'asx-listed.csv').exists():
                    missing.append('ASX 名单不可用' if zh else 'ASX list unavailable')
                if not list(directory.glob('ato-tax-*.xlsx')):
                    missing.append('ATO 名单不可用' if zh else 'ATO list unavailable')
                if missing:
                    items.append(('资料状态：' if zh else 'Reference availability: ') + '；'.join(missing))
                items.append('名单记录不保证当前岗位提供担保；未匹配也不表示企业不能担保。' if zh else 'A list entry does not guarantee sponsorship for this job; no match does not establish ineligibility.')
                views[lang]['sections'].append({'title': '雇主信息' if zh else 'Employer facts',
                    'status': _summary(lang, acc, sanction, bool(accredited_files), abf_available), 'items': items})
        results[job['uid']] = views
    return {'analysis_id': request['analysis_id'], 'results': results}
