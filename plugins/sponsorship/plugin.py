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
    zh = lang == 'zh'
    if sanction:
        return ('⚠ 有违规担保记录' if sanction == 'confirmed' else '⚠ 可能有违规记录') if zh else ('⚠ Sponsorship breach recorded' if sanction == 'confirmed' else '⚠ Possible sponsorship breach')
    missing = []
    if not acc_available:
        missing.append('认证担保名单' if zh else 'Accredited sponsor list')
    if not abf_available:
        missing.append('违规名单' if zh else 'Sanctions list')
    if missing:
        return ('、'.join(missing) + '暂不可用') if zh else ('; '.join(missing) + ' unavailable')
    if acc:
        return ('认证担保雇主 ✓' if acc == 'exact' else '可能是认证担保雇主') if zh else ('Accredited sponsor ✓' if acc == 'exact' else 'Possible accredited sponsor')
    return '未见违规记录' if zh else 'No sponsorship breach found'


def _source_date(value, lang, *, month=False):
    """Format reference dates without exposing storage filenames."""
    try:
        date = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return '日期未知' if lang == 'zh' else 'date unknown'
    label = date.strftime('%Y-%m' if month else '%Y-%m-%d')
    if (datetime.now(timezone.utc).date() - date).days > 365:
        label += '，较旧' if lang == 'zh' else ', older source'
    return label


def after_analyze_success(request):
    directory = Path(request['data_dir'])
    accredited = ref.NameIndex(ref.load_accredited(directory))
    sanctions = ref.load_sanctions(directory)
    companies = ref.Companies(directory, ref.NameIndex)
    accredited_files = sorted(directory.glob('accredited-*.txt'))
    abf_available = (directory / 'sanctioned.json').exists()
    asx_available = (directory / 'asx-listed.csv').exists()
    ato_available = bool(list(directory.glob('ato-tax-*.xlsx')))
    source_lines = {}
    asx_sources = {}
    ato_sources = {}
    for lang in ('zh', 'en'):
        zh = lang == 'zh'
        sources = []
        if accredited_files:
            dates = ' · '.join(dict.fromkeys(
                _source_date(path.stem.removeprefix('accredited-'), lang, month=True)
                for path in accredited_files))
            sources.append(f'Home Affairs 认证名单（{dates}）' if zh else f'Home Affairs accredited list ({dates})')
        if abf_available:
            date = _source_date(_date(directory / 'sanctioned.json'), lang)
            sources.append(f'ABF 违规名单（下载 {date}）' if zh else f'ABF sanctions register (downloaded {date})')
        if asx_available:
            date = _source_date(_date(directory / 'asx-listed.csv'), lang)
            asx_sources[lang] = f'ASX 上市名单（下载 {date}）' if zh else f'ASX listed-company directory (downloaded {date})'
        if ato_available:
            year = companies.ato_year or ('年度未知' if zh else 'year unknown')
            ato_sources[lang] = f'ATO 企业税务透明度报告（{year}）' if zh else f'ATO corporate tax transparency report ({year})'
        source_lines[lang] = sources
    results = {}
    for job in request['jobs']:
        company = job.get('company') or ''
        views = {lang: {'badges': [], 'sections': []} for lang in ('zh', 'en')}
        if not company.strip() or ref._AGENCY.search(company) or re.search(r'\b(anonymous|confidential|undisclosed)\b', company, re.I):
            for lang in ('zh', 'en'):
                views[lang]['sections'].append({
                    'title': '雇主信息' if lang == 'zh' else 'Employer facts',
                    'status': '实际雇主未知' if lang == 'zh' else 'Actual employer unknown',
                    'items': ['广告使用招聘机构或匿名名称，暂无法核对实际雇主的名单记录。' if lang == 'zh' else 'The advert uses an agency or anonymous name, so the actual employer cannot be checked.']})
        else:
            acc, acc_name = accredited.match(company)
            sanction, sanction_row = sanctions.match(company)
            size = companies.lookup(company)
            for lang in ('zh', 'en'):
                zh = lang == 'zh'
                items = []
                sources = list(source_lines[lang])
                if sanction:
                    label = ('违规名单确认匹配' if sanction == 'confirmed' else '可能关联违规名单') if zh else ('Confirmed sanctions match' if sanction == 'confirmed' else 'Possible sanctions match')
                    date = sanction_row.get('date') or ('未知' if zh else 'unknown')
                    name = sanction_row.get('name', '')
                    if sanction == 'confirmed':
                        evidence = (f'⚠ 有违规担保记录\n曾因违反担保义务受到处罚。名单名称：{name}；记录日期：{date}。' if zh else f'⚠ Sponsorship breach recorded\nThe employer was sanctioned for breaching sponsorship obligations. Listed name: {name}; record date: {date}.')
                    else:
                        evidence = (f'⚠ 可能有违规记录\n名称与违规名单中的 {name} 相近，请核对公司全称。记录日期：{date}。' if zh else f'⚠ Possible sponsorship breach\nThe name resembles {name} on the sanctions register. Check the full company name. Record date: {date}.')
                    items.append(evidence)
                    views[lang]['badges'].append({'text': label, 'level': 'warn', 'tooltip': evidence})
                elif abf_available:
                    items.append('✓ 未见违规担保记录\n澳洲边境执法局（ABF）公布的违规担保雇主中没有这家公司。' if zh else '✓ No sponsorship breach found\nThe company is not on the Australian Border Force (ABF) register of sanctioned sponsors.')
                else:
                    items.append('违规名单暂不可用\n暂时无法核对这家公司的违规担保记录。' if zh else 'Sanctions list unavailable\nSponsorship breach records cannot be checked at the moment.')
                if acc:
                    label = ('认证担保名单匹配' if acc == 'exact' else '认证名单可能匹配') if zh else ('Accredited list match' if acc == 'exact' else 'Possible accredited list match')
                    if acc == 'exact':
                        evidence = (f'✓ 认证担保雇主\n名单将 {acc_name} 列为移民局认证的担保方，可享相应审批便利。' if zh else f'✓ Accredited sponsor\nThe list records {acc_name} as a Home Affairs accredited sponsor, with access to accreditation benefits.')
                    else:
                        evidence = (f'可能是认证担保雇主\n名称与认证名单中的 {acc_name} 相近，请核对公司全称。' if zh else f'Possible accredited sponsor\nThe name resembles {acc_name} on the accredited sponsor list. Check the full company name.')
                    if abf_available and not sanction:
                        items.insert(0, evidence)
                    else:
                        items.append(evidence)
                    views[lang]['badges'].append({'text': label, 'level': 'info', 'tooltip': evidence})
                elif accredited_files:
                    items.append('– 不在认证担保雇主名单内\n普通担保雇主也能提供担保；这份名单只列获移民局额外认证的雇主。' if zh else '– Not on the accredited sponsor list\nStandard business sponsors can also offer sponsorship. This list covers only those with additional accreditation.')
                else:
                    items.append('认证担保名单暂不可用\n暂时无法核对这家公司是否在认证名单中。' if zh else 'Accredited sponsor list unavailable\nThe company cannot be checked against the accredited sponsor list at the moment.')
                if size['asx']:
                    sources.append(asx_sources[lang])
                    heading = ('✓ ASX 上市公司' if size['asx'] == 'exact' else '可能是 ASX 上市公司') if zh else ('✓ ASX-listed company' if size['asx'] == 'exact' else 'Possible ASX-listed company')
                    explanation = (f'上市公司名单中列有 {size["asx_name"]}。' if zh else f'The listed-company directory includes {size["asx_name"]}.') if size['asx'] == 'exact' else (f'名称与上市公司 {size["asx_name"]} 相近，请核对公司全称。' if zh else f'The name resembles the listed company {size["asx_name"]}. Check the full company name.')
                    items.append(heading + '\n' + explanation)
                if size['ato']:
                    sources.append(ato_sources[lang])
                    heading = ('✓ 列入 ATO 企业税务透明度报告' if size['ato'] == 'exact' else '可能列入 ATO 企业税务透明度报告') if zh else ('✓ Included in the ATO corporate tax transparency report' if size['ato'] == 'exact' else 'Possible entry in the ATO corporate tax transparency report')
                    explanation = (f'报告列有 {size["ato_name"]}，可作为企业规模参考。' if zh else f'The report includes {size["ato_name"]}, providing a reference for company scale.') if size['ato'] == 'exact' else (f'名称与报告中的 {size["ato_name"]} 相近，请核对公司全称。' if zh else f'The name resembles {size["ato_name"]} in the report. Check the full company name.')
                    items.append(heading + '\n' + explanation)
                if not asx_available:
                    items.append('ASX 上市名单暂不可用。' if zh else 'ASX listed-company directory unavailable.')
                if not ato_available:
                    items.append('ATO 企业税务透明度报告暂不可用。' if zh else 'ATO corporate tax transparency report unavailable.')
                items.append('能否担保，以岗位描述或直接向雇主确认为准。' if zh else 'Check the job description or ask the employer whether this role offers sponsorship.')
                items.append(('来源：' if zh else 'Sources: ') + (' · '.join(sources) if sources else ('暂无可用名单。' if zh else 'No reference lists available.')))
                views[lang]['sections'].append({'title': '雇主信息' if zh else 'Employer facts',
                    'status': _summary(lang, acc, sanction, bool(accredited_files), abf_available), 'items': items})
        results[job['uid']] = views
    return {'analysis_id': request['analysis_id'], 'results': results}
