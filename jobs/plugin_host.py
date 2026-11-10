"""Read-only discovery and isolated execution of optional local plugins.

Installed plugins are trusted code, not sandboxed. The process boundary contains
accidental crashes and hangs; it is not a security boundary against hostile code.
"""
from __future__ import annotations

import json
import hashlib
import stat
import time
from contextlib import closing
import os
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import RunTimeout
from .config import ROOT, db_path
from .run_state import atomic_json

PLUGIN_ROOT = ROOT / 'plugins'
MAX_MANIFEST = 32 * 1024
MAX_RESULT = 8 * 1024 * 1024
MAX_REQUEST = 8 * 1024 * 1024
MAX_LOG = 64 * 1024
TIMEOUT_ANALYZE = 30
TIMEOUT_FETCH = 180
_ID = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_EMPTY = {'badges': [], 'sections': []}
SPONSOR_NETWORK = [{'purpose': 'sponsorship_reference', 'timing': 'after_fetch',
                    'targets': ['www.abf.gov.au', 'www.asx.com.au', 'data.gov.au'],
                    'request_data': 'no_job_data'}]
PAGE_ICONS = ('map', 'workspace', 'compare')   # icons the core draws; plugins supply no markup
PAGE_JOB_FIELDS = ['uid', 'group_id', 'title', 'company', 'location_raw',
                   'location_fact', 'salary', 'work_type', 'listing_date',
                   'total_score', 'priority', 'core_order', 'panel_url']
HANDED_JOB_DETAIL = ['description', 'skills', 'tags', 'qualification',
                     'eligibility_evidence', 'human_labels']
MAP_NETWORK = [{'purpose': 'map_tiles', 'timing': 'view_open',
                'targets': ['tiles.openfreemap.org'],
                'request_data': 'tile_coordinates_and_browser_metadata'}]


def _read(path, limit):
    # No FIFO/device/symlink reads on a web request, including damaged installs.
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('invalid file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('file too large')
    return json.loads(data.decode('utf-8'))


def _root(config):
    db = db_path(config).resolve()
    return db.parent / ('.' + db.name + '.plugins')


def _manifest(path, plugin_id):
    value = _read(path / 'manifest.json', MAX_MANIFEST)
    if not isinstance(value, dict) or value.get('id') != plugin_id:
        raise ValueError('invalid manifest id')
    if not isinstance(value.get('version'), str) or not value['version'] or len(value['version']) > 64:
        raise ValueError('invalid version')
    name = value.get('name')
    if not (isinstance(name, str) and 0 < len(name) <= 100 or
            isinstance(name, dict) and name and all(k in ('zh', 'en') and isinstance(v, str) and 0 < len(v) <= 100 for k, v in name.items())):
        raise ValueError('invalid name')
    description = value.get('description', '')
    if not (isinstance(description, str) and len(description) <= 200 or
            isinstance(description, dict) and all(k in ('zh', 'en') and isinstance(v, str) and len(v) <= 200 for k, v in description.items())):
        raise ValueError('invalid description')
    if value.get('compatibility') == {'host_api': 2}:
        return _page_manifest(value)
    if value.get('compatibility') != {'host_api': 1}:
        raise ValueError('incompatible host API')
    if value.get('entry') != 'plugin.py':
        raise ValueError('invalid entry')
    if not isinstance(value.get('hooks'), list) or not set(value['hooks']) <= {'after_fetch', 'after_analyze_success'} or any(not isinstance(x, str) for x in value['hooks']):
        raise ValueError('invalid hooks')
    if value.get('network') not in ('after_fetch', 'none') or not isinstance(value.get('display'), bool) or not isinstance(value.get('i18n'), list) or not set(value['i18n']) <= {'zh', 'en'}:
        raise ValueError('invalid capabilities')
    permissions = value.get('permissions')
    if not isinstance(permissions, dict) or set(permissions) != {'job_fields', 'files', 'network', 'execution', 'display'}:
        raise ValueError('missing permissions')
    if (permissions['job_fields'] != ['uid', 'company'] or
            permissions['files'] != ['plugin_data'] or
            permissions['execution'] != ['python_worker'] or
            permissions['display'] != ['job_badges', 'job_details'] or
            permissions['network'] not in ([], SPONSOR_NETWORK)):
        raise ValueError('unknown permissions')
    if (value['network'] == 'after_fetch') != bool(permissions['network']):
        raise ValueError('inconsistent network permission')
    return value


def _label(value, limit=32):
    return (isinstance(value, dict) and set(value) == {'zh', 'en'}
            and all(isinstance(v, str) and 0 < len(v) <= limit for v in value.values()))


def _registration(value, plugin_id, entry, keys, required=('id', 'label', 'entry')):
    return (isinstance(value, dict) and set(required) <= set(value) <= set(keys)
            and value.get('icon', 'map') in PAGE_ICONS and value['id'] == plugin_id
            and value['entry'] == entry and _label(value['label']))


def _page_manifest(value):
    """Structured, front-end-only pages. Never imports installed Python code.
    Two exact permission profiles exist: the map page and handed jobs."""
    if value.get('network') == 'none' or 'sidebar' in value:
        return _handed_manifest(value)
    page = value.get('page')
    if not _registration(page, value['id'], 'web/index.html', ('id', 'label', 'entry', 'icon')):
        raise ValueError('invalid page')
    if (value.get('entry') != page['entry'] or value.get('hooks') != []
            or value.get('network') != 'view_open' or value.get('display') is not True
            or value.get('i18n') != ['zh', 'en']):
        raise ValueError('invalid page capabilities')
    if value.get('permissions') != {
            'job_fields': PAGE_JOB_FIELDS, 'files': ['plugin_reference'],
            'network': MAP_NETWORK, 'execution': ['browser_frame'],
            'display': ['page', 'display_mode']}:
        raise ValueError('unknown page permissions')
    return value


def _handed_manifest(value):
    """A sidebar that reads only jobs the user handed to it, plus a page."""
    sidebar, page = value.get('sidebar'), value.get('page')
    if (not _registration(sidebar, value['id'], 'web/index.html',
                          ('id', 'label', 'entry', 'icon', 'handoff_label'),
                          ('id', 'label', 'entry', 'handoff_label'))
            or not _label(sidebar['handoff_label'])):
        raise ValueError('invalid sidebar')
    if (not _registration(page, value['id'], 'web/compare.html', ('id', 'label', 'entry', 'icon', 'switch'))
            or not isinstance(page.get('switch', False), bool)):
        raise ValueError('invalid page')
    if (value.get('entry') != sidebar['entry'] or value.get('hooks') != []
            or value.get('network') != 'none' or value.get('display') is not True
            or value.get('i18n') != ['zh', 'en']):
        raise ValueError('invalid page capabilities')
    permissions = value.get('permissions')
    base = {'job_fields': PAGE_JOB_FIELDS, 'job_scope': 'handed', 'files': [], 'network': [],
            'execution': ['browser_frame'], 'display': ['sidebar', 'page', 'handoff']}
    if permissions not in (base, {**base, 'job_detail': HANDED_JOB_DETAIL}):
        raise ValueError('unknown page permissions')
    return value


def handed(manifest):
    """True for the handed-jobs profile (sidebar + page, no list access)."""
    return (manifest.get('compatibility') == {'host_api': 2}
            and (manifest.get('permissions') or {}).get('job_scope') == 'handed')


def fingerprint(manifest):
    scope = {key: manifest.get(key) for key in ('id', 'hooks', 'network', 'display', 'permissions')}
    if manifest.get('compatibility') == {'host_api': 2}:
        # `sidebar` only joins the scope when declared, so map fingerprints are unchanged.
        keys = ('compatibility', 'entry', 'page') + (('sidebar',) if 'sidebar' in manifest else ())
        scope.update({key: manifest.get(key) for key in keys})
        for key in ('page', 'sidebar'):   # display icons are cosmetic, not permissions
            if isinstance(scope.get(key), dict):
                scope[key] = {k: v for k, v in scope[key].items() if k != 'icon'}
    return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _grant_path(config, plugin_id):
    return _root(config) / plugin_id / 'consent.json'


def consent(config, manifest):
    current = fingerprint(manifest)
    try:
        saved = _read(_grant_path(config, manifest['id']), MAX_LOG)
        previous = saved.get('fingerprint') if isinstance(saved, dict) else None
    except RunTimeout:
        raise
    except Exception:
        previous = None
    status = 'granted' if previous == current else ('changed' if previous else 'required')
    if handed(manifest):
        return {'status': status, 'fingerprint': current,
                'permissions': _handed_consent(manifest), 'limits': _handed_limits()}
    if manifest.get('compatibility') == {'host_api': 2}:
        entries = [
            ('identity', '插件来源', 'Plugin source',
             '请只安装来自可信维护者的插件。', 'Only install plugins from maintainers you trust.'),
            ('jobs', '读取岗位摘要', 'Reads job summaries',
             '读取当前筛选结果中每个岗位的标题、公司、地点、薪资、工作类型、发布日期、匹配分和星标，用来在地图上显示。不读取岗位正文、备注、历史记录、简历或账号信息。',
             'Reads the title, company, location, salary, work type, listing date, score and star of each job in the current results to show them on the map. It never reads job descriptions, notes, history, your CV or account details.'),
            ('files', '自带的邮编数据', 'Built-in postcode data',
             '使用插件自带的澳洲统计局公开邮编范围数据，不需要联网下载。地图位置只记在当前浏览器标签页里；关闭或卸载插件不会删除这些数据文件。',
             'Uses public Australian Bureau of Statistics postcode areas shipped with the plugin; nothing is downloaded. The map position is only kept in this browser tab. Turning off or removing the plugin does not delete these data files.'),
            ('network', '加载底图', 'Loads the background map',
             '只在打开地图时，浏览器会从 tiles.openfreemap.org 加载底图。对方能看到你的 IP 地址、正在查看的地图范围，以及你访问看板用的网址（只含开头的地址，不含具体页面），但收不到岗位、公司、正文、备注或搜索词。关闭插件后不再加载。',
             'Only while the map is open, your browser loads the background map from tiles.openfreemap.org. They can see your IP address, which part of the map you are viewing and the address you use to open this board (the site address only, not the page), but never jobs, companies, descriptions, notes or searches. Nothing loads after you turn the plugin off.'),
            ('execution', '独立运行', 'Runs separately',
             '地图在看板中一块独立区域里运行，只能拿到上面的岗位摘要，或请看板打开某个岗位的详情。它不能修改岗位、标注或备注。',
             'The map runs in a separate area of the board. It can only get the job summaries above or ask the board to open a job. It cannot change jobs, labels or notes.'),
            ('display', '列表 / 地图切换', 'List / map switch',
             '在搜索栏旁加入列表和地图的切换按钮。在地图上移动或选择区域只影响地图，不会改变看板的筛选、排序、匹配分或你的标注。',
             'Adds a list / map switch next to the search box. Moving around or picking an area on the map only affects the map, never the board’s filters, sorting, scores or your labels.'),
        ]
        return {'status': status, 'fingerprint': current,
                'permissions': [{'id': i, 'title': {'zh': z, 'en': e},
                                 'description': {'zh': dz, 'en': de}}
                                for i, z, e, dz, de in entries],
                'limits': {'zh': '请只使用可信来源的插件。地图在一块独立区域里运行，能加载的内容和联网范围都有限制，读不到你的文件，也改不了看板里的数据。但这些限制不能完全防住恶意插件，比如它仍可能让这块区域跳转到别的网页，所以请不要安装来源不明的插件。',
                           'en': 'Only use plugins you trust. The map is kept in a separate area with limits on what it can load and connect to, and it cannot read your files or change data on the board. These limits are not airtight: a plugin could still send its own area to another web page, so never install a plugin from a source you do not trust.'}}
    entries = [
        ('identity', '插件来源', 'Plugin source',
         '这是本机安装的插件，请确认安装包来自你信任的维护者。', 'This plugin is installed locally. Make sure its package comes from a maintainer you trust.'),
        ('jobs', '岗位资料', 'Job data', '读取岗位标识和公司名称，用于匹配雇主。', 'Reads job identifier and company name to match employers.'),
        ('files', '文件和保留', 'Files and retention', '使用插件资料、结果和错误记录目录；关闭或卸载后保留。', 'Uses plugin data, results and error records; these remain after disable or uninstall.'),
        ('execution', '本机执行', 'Local execution', '在 Python 子进程中运行插件。', 'Runs plugin code in a Python child process.'),
        ('display', '展示', 'Presentation', '可在岗位卡片及详情显示结果，不修改核心岗位数据。', 'May show results on job cards and details; does not change core job data.'),
    ]
    if manifest['permissions']['network']:
        entries.insert(3, ('network', '公开资料更新', 'Public reference updates',
                          '抓取后由宿主从 www.abf.gov.au、www.asx.com.au 和 data.gov.au 下载公开名单及报告，不向这些网站发送岗位资料。',
                          'After fetch, the host downloads public lists and reports from www.abf.gov.au, www.asx.com.au and data.gov.au. It does not send job data to these sites.'))
    else:
        entries.insert(3, ('network', '网络', 'Network', '未声明联网。', 'No network access declared.'))
    return {'status': status, 'fingerprint': current,
            'permissions': [{'id': i, 'title': {'zh': z, 'en': e}, 'description': {'zh': dz, 'en': de}} for i, z, e, dz, de in entries],
            'limits': {'zh': '宿主限制常规 Python 文件访问：可读插件代码、资料、临时目录和 Python 运行库，只可写资料及临时目录。插件不能直接联网或启动外部程序；已授权的公开资料下载由宿主完成。这些限制适用于可信 Python 插件，不能安全运行恶意代码。',
                       'en': 'The host restricts ordinary Python file access: plugins can read their code, data, temporary files and Python libraries, and write only data and temporary files. Direct networking and external programs are blocked; the host performs approved public downloads. These limits are for trusted Python plugins, not hostile code.'}}


def _handed_consent(manifest):
    detail = 'job_detail' in manifest['permissions']
    entries = [
        ('identity', '插件来源', 'Plugin source',
         '请只安装来自可信维护者的插件。', 'Only install plugins from maintainers you trust.'),
        ('jobs', '只读取你放进侧栏的岗位', 'Reads only jobs you put in the sidebar',
         ('只有你亲手放进侧栏的岗位才会被读取，最多 10 个。会读取标题、公司、地点、薪资、工作类型、发布日期、匹配分和星标，'
          '还会读取岗位正文、技能、规则判断结果和你的标注状态（比如资格、处理进度和星标）。不读取备注内容、历史记录、简历或账号信息，也读不到其他岗位。'
          if detail else
          '只有你亲手放进侧栏的岗位才会被读取，最多 10 个。只读取标题、公司、地点、薪资、工作类型、发布日期、匹配分、星标和你的标注状态。'
          '不读取岗位正文、备注内容、历史记录、简历或账号信息，也读不到其他岗位。'),
         ('It only reads jobs you put in the sidebar yourself, up to 10. It reads their title, company, location, salary, work type, '
          'listing date, score and star, plus the full job description, skills, rule results and your label states (such as eligibility, '
          'progress and star). It never reads note text, history, your CV or account details, and cannot see other jobs.'
          if detail else
          'It only reads jobs you put in the sidebar yourself, up to 10: their title, company, location, salary, work type, listing date, '
          'score, star and your label states. It never reads job descriptions, note text, history, your CV or account details, and cannot see other jobs.')),
        ('files', '不使用文件', 'No files',
         '插件不带数据文件，也读不到你的文件。侧栏里的岗位和插件设置只记在这个浏览器里。',
         'The plugin ships no data files and cannot read your files. The jobs in the sidebar and its settings are only kept in this browser.'),
        ('network', '不联网', 'No network',
         '插件不能连接任何外部网站。', 'The plugin cannot connect to any outside website.'),
        ('execution', '独立运行', 'Runs separately',
         ('侧栏和对比页各在看板中一块独立区域里运行，只能拿到上面的岗位资料，或请看板打开某个岗位。它不能修改岗位、标注或备注。'
          '它生成的给 agent 的指令只包含岗位编号，不含岗位内容。'),
         ('The sidebar and the compare page each run in a separate area of the board. They can only get the job data above or ask '
          'the board to open a job. They cannot change jobs, labels or notes. The instruction it writes for an agent contains only job IDs, no job content.')),
        ('display', '侧栏、加入按钮和对比页', 'Sidebar, add action and compare page',
         '在看板旁加入一个侧栏，在岗位上加入“加入”操作，并加入一个对比页。它不会改变看板的筛选、排序、匹配分或你的标注。',
         'Adds a sidebar beside the board, an “add” action on jobs and a compare page. It never changes the board’s filters, sorting, scores or your labels.'),
    ]
    return [{'id': i, 'title': {'zh': z, 'en': e}, 'description': {'zh': dz, 'en': de}}
            for i, z, e, dz, de in entries]


def _handed_limits():
    return {'zh': '请只使用可信来源的插件。侧栏和对比页在独立区域里运行，不能联网，读不到你的文件，也改不了看板里的数据。但这些限制不能完全防住恶意插件，比如它仍可能让这块区域跳转到别的网页，所以请不要安装来源不明的插件。',
            'en': 'Only use plugins you trust. The sidebar and compare page are kept in separate areas with no network access; they cannot read your files or change data on the board. These limits are not airtight: a plugin could still send its own area to another web page, so never install a plugin from a source you do not trust.'}


def grant(config, manifest, expected, channel):
    if expected != fingerprint(manifest):
        raise ValueError('fingerprint changed')
    path = _grant_path(config, manifest['id'])
    try:
        atomic_json(path, {'fingerprint': expected, 'plugin_id': manifest['id'],
                    'actor': 'local_user', 'channel': channel, 'time': datetime.now(timezone.utc).isoformat()})
        # Windows fsync is FlushFileBuffers, which needs a writable handle.
        with path.open('r+b') as stream:
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
    except OSError:
        path.unlink(missing_ok=True)
        raise


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def revoke(config, plugin_id):
    path = _grant_path(config, plugin_id)
    if path.exists():
        path.unlink()
        _sync_directory(path.parent)


def _enabled(config, plugin_id):
    plugins = config.get('plugins', {})
    if not isinstance(plugins, dict):
        raise ValueError('invalid plugins configuration')
    section = plugins.get(plugin_id, {})
    if not isinstance(section, dict) or not isinstance(section.get('enabled', False), bool):
        raise ValueError('invalid plugin configuration')
    return section.get('enabled', False)


def read_debug(config: dict, plugin_id: str) -> dict:
    result = {'plugin_id': plugin_id, 'entries': []}
    if not isinstance(plugin_id, str) or not _ID.fullmatch(plugin_id):
        return result
    try:
        value = _read(_root(config) / plugin_id / 'debug.json', MAX_LOG)
        entries = value.get('entries') if isinstance(value, dict) else None
        if isinstance(entries, list):
            result['entries'] = [e for e in entries[-100:] if isinstance(e, dict) and all(isinstance(e.get(k), str) and len(e[k]) <= 256 for k in ('time', 'stage', 'error', 'message'))]
    except RunTimeout:
        raise
    except Exception:
        pass
    return result


def _log(config, plugin_id, stage, error, message="Plugin operation failed."):
    # Never persist exception messages, plugin stdout or configuration secrets.
    try:
        entries = read_debug(config, plugin_id)['entries'][-99:]
        entries.append({'time': datetime.now(timezone.utc).isoformat(), 'stage': stage[:64],
                        'error': error[:64], 'message': message[:256]})
        atomic_json(_root(config) / plugin_id / 'debug.json', {'entries': entries})
    except RunTimeout:
        raise
    except Exception:
        pass


def list_plugins(config: dict) -> list[dict]:
    if 'demo' in config:
        return []
    try:
        paths = sorted(PLUGIN_ROOT.iterdir())[:32]
    except OSError:
        return []
    result = []
    for path in paths:
        plugin_id = path.name
        if not _ID.fullmatch(plugin_id) or not path.is_dir() or path.is_symlink():
            continue
        item = {'id': plugin_id, 'name': plugin_id, 'version': '', 'enabled': False,
                'description': '', 'status': 'disabled', 'error_count': len(read_debug(config, plugin_id)['entries'])}
        try:
            item['enabled'] = _enabled(config, plugin_id)
            manifest = _manifest(path, plugin_id)
            item.update(name=manifest['name'], version=manifest['version'], description=manifest.get('description', ''))
            item['consent'] = consent(config, manifest)
            item['status'] = ('enabled' if item['consent']['status'] == 'granted' else 'consent_required') if item['enabled'] else 'disabled'
        except RunTimeout:
            raise
        except Exception:
            item['status'] = 'error'
        result.append(item)
    return result


def _display_manifests(config):
    """(id, manifest) for authorized, compatible host API 2 plugins; disabled path is inert."""
    if not _configured(config) or 'demo' in config:
        return []
    found = []
    for item in list_plugins(config):
        if item['status'] != 'enabled':
            continue
        try:
            manifest = _manifest(PLUGIN_ROOT / item['id'], item['id'])
            if manifest.get('compatibility') == {'host_api': 2}:
                found.append((item['id'], manifest))
        except RunTimeout:
            raise
        except (OSError, ValueError, TypeError):
            continue
    return found


def display_manifest(config, plugin_id):
    """The validated manifest of an authorized host API 2 plugin, else None."""
    return next((m for i, m in _display_manifests(config) if i == plugin_id), None)


def _page_item(plugin_id, manifest):
    page = manifest['page']
    # Map pages are List/Map switch pages; handed pages only when they say so.
    switch = page.get('switch', False) if handed(manifest) else True
    return {'id': plugin_id, 'label': page['label'], 'icon': page.get('icon'), 'switch': switch}


def page_plugins(config):
    """Only authorized, compatible display registrations; disabled path is inert.
    Only items with `switch` belong in the toolbar List/Map switch."""
    return [_page_item(i, m) for i, m in _display_manifests(config)]


def sidebar_plugins(config):
    """Authorized sidebar registrations: [{id, label, handoff_label, icon}]."""
    return [{'id': i, 'label': m['sidebar']['label'], 'handoff_label': m['sidebar']['handoff_label'],
             'icon': m['sidebar'].get('icon')}
            for i, m in _display_manifests(config) if handed(m)]


def _latest(config):
    # Opening in read-only mode avoids creating a database during presentation.
    uri = db_path(config).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
        row = conn.execute("SELECT run_id FROM runs WHERE kind='analyze' ORDER BY run_id DESC LIMIT 1").fetchone()
        return row[0] if row else None


def _valid_view(view):
    if not isinstance(view, dict) or set(view) != {'badges', 'sections'}:
        return False
    badges, sections = view['badges'], view['sections']
    if not isinstance(badges, list) or not isinstance(sections, list) or len(badges) > 20 or len(sections) > 20:
        return False
    def string(x): return isinstance(x, str) and len(x) <= 1000
    return all(isinstance(b, dict) and set(b) == {'text', 'level', 'tooltip'} and string(b['text']) and b['level'] in ('good', 'warn', 'bad', 'info') and string(b['tooltip']) for b in badges) and all(isinstance(s, dict) and set(s) == {'title', 'status', 'items'} and string(s['title']) and string(s['status']) and isinstance(s['items'], list) and len(s['items']) <= 40 and all(string(i) for i in s['items']) for s in sections)


def presentation_many(config: dict, uids: list[str], lang: str = 'zh') -> dict:
    """Read each installed plugin once per page, never once per card."""
    result = {uid: {'badges': [], 'sections': []} for uid in uids}
    if 'demo' in config or not config.get('plugins'):
        return result
    try:
        generation = _latest(config)
        if generation is None:
            return result
        for item in list_plugins(config):
            if item['status'] != 'enabled':
                continue
            try:
                value = _read(_root(config) / item['id'] / 'results.json', MAX_RESULT)
                if (not isinstance(value, dict) or value.get('analysis_id') != generation
                        or not isinstance(value.get('results'), dict)):
                    continue
                shared = value.get('views') if isinstance(value.get('views'), dict) else {}
                for uid in uids:
                    entry = value['results'].get(uid, {})
                    if isinstance(entry, str):
                        entry = shared.get(entry, {})
                    view = entry.get(lang if lang in ('zh', 'en') else 'zh') if isinstance(entry, dict) else None
                    if _valid_view(view):
                        result[uid]['badges'].extend(view['badges'][:4])
                        result[uid]['sections'].extend(view['sections'][:6])
            except RunTimeout:
                raise
            except Exception:
                continue
    except RunTimeout:
        raise
    except Exception:
        pass
    return result


def presentation(config: dict, uid: str, lang: str = 'zh') -> dict:
    return presentation_many(config, [uid], lang).get(uid, {'badges': [], 'sections': []})


class WorkerError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(code)


def _wait_worker(process, hook):
    budget = TIMEOUT_FETCH if hook == 'after_fetch' else TIMEOUT_ANALYZE
    deadline = time.monotonic() + budget
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, budget)
        try:
            return process.wait(timeout=min(remaining, .25))
        except subprocess.TimeoutExpired:
            if sys.platform == 'darwin':
                # macOS ignores RLIMIT_AS. Observe RSS for the entire process group.
                memory = subprocess.run(['/bin/ps', '-o', 'rss=', '-g', str(process.pid)],
                                        capture_output=True, timeout=2, text=True)
                if memory.returncode == 0 and sum(int(n) for n in memory.stdout.split()) > 512 * 1024:
                    raise WorkerError('MemoryLimit', 'Plugin exceeded the 512 MiB memory budget.')


def _run_worker(config, plugin_id, hook, request):
    return _run_isolated(config, plugin_id, hook, request)


def _refresh_references(config, plugin_id, directory):
    return _run_isolated(config, plugin_id, 'after_fetch',
                         {'data_dir': str(directory)}, trusted_download=True)


def _run_isolated(config, plugin_id, hook, request, *, trusted_download=False):
    payload = json.dumps(request, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_REQUEST:
        raise ValueError('request too large')
    with tempfile.TemporaryDirectory(prefix='jobs-plugin-') as tmp:
        request_path, result_path = Path(tmp) / 'input.json', Path(tmp) / 'output.json'
        request_path.write_bytes(payload)
        request_path.chmod(0o600)
        command = ([sys.executable, '-m', 'jobs.plugin_downloads', str(request_path), str(result_path)]
                   if trusted_download else
                   [sys.executable, '-m', 'jobs.plugin_worker', str(PLUGIN_ROOT / plugin_id / 'plugin.py'), hook, str(request_path), str(result_path)])
        process = job = lock = None
        try:
            if os.name == 'nt':
                from . import windows_process
                lock = open(os.devnull, 'rb')
                job = windows_process.WindowsJob(memory_limit=512 * 1024 * 1024)
                raw = windows_process.lock_handle(lock)
                with windows_process._INHERITANCE_LOCK:
                    try:
                        os.set_handle_inheritable(raw, True)
                        process = windows_process._create_in_job(job, command, raw, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    finally:
                        os.set_handle_inheritable(raw, False)
            else:
                environment = {key: value for key, value in os.environ.items() if key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LANG', 'LC_ALL')}
                # The worker exits if this process dies, even when killed outright.
                environment['JOBS_PLUGIN_PARENT'] = str(os.getpid())
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=environment)
            _wait_worker(process, hook)
            if process.returncode != 0:
                try:
                    failure = _read(Path(tmp) / 'error.json', MAX_LOG)
                    code = failure.get('error')
                    location = failure.get('location')
                    if isinstance(code, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', code) and isinstance(location, str):
                        raise WorkerError(code, 'Plugin failed at ' + location[:180])
                except WorkerError:
                    raise
                except RunTimeout:
                    raise
                except Exception:
                    pass
                raise WorkerError('WorkerExit', 'Plugin process exited with code ' + str(process.returncode))
            return _read(result_path, MAX_RESULT)
        finally:
            if process is not None:
                if os.name == 'nt':
                    try:
                        job.stop(process)
                    finally:
                        process.close()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
            if job is not None:
                job.close()
            if lock is not None:
                lock.close()


def _dispatch(config: dict, hook: str, jobs: list | None = None, analysis_id: int | None = None) -> None:
    """Invoke enabled hooks; caller owns the run lock, never a DB transaction."""
    configured = config.get('plugins')
    if 'demo' in config or not isinstance(configured, dict) or not configured:
        return
    if all(isinstance(value, dict) and value.get('enabled', False) is False for value in configured.values()):
        return
    for item in list_plugins(config):
        if item['status'] != 'enabled':
            if item['status'] == 'error' and item['id'] in configured:
                _log(config, item['id'], hook, 'ConfigurationError')
            continue
        plugin_id = item['id']
        try:
            manifest = _manifest(PLUGIN_ROOT / plugin_id, plugin_id)
            if consent(config, manifest)['status'] != 'granted':
                continue
            if hook not in manifest['hooks']:
                continue
            directory = _root(config) / plugin_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if hook == 'after_fetch' and plugin_id == 'sponsorship' and manifest['permissions']['network'] == SPONSOR_NETWORK:
                if _refresh_references(config, plugin_id, directory / 'data').get('errors'):
                    _log(config, plugin_id, hook, 'RefreshError')
            request = {'api': 1, 'hook': hook, 'data_dir': str(directory / 'data'),
                       'config': {}, 'jobs': [{k: j.get(k) for k in ('uid', 'company')} for j in jobs or []], 'analysis_id': analysis_id,
                       'allow_network': False}
            value = _run_worker(config, plugin_id, hook, request)
            if hook == 'after_fetch' and isinstance(value, dict) and value.get('errors'):
                _log(config, plugin_id, hook, 'RefreshError')
            if hook == 'after_analyze_success':
                if (not isinstance(value, dict) or set(value) - {'views'} != {'analysis_id', 'results'}
                        or value['analysis_id'] != analysis_id or not isinstance(value['results'], dict)):
                    raise ValueError('invalid result')
                # Optional shared views: a result may name one instead of repeating
                # identical bilingual text for every job of the same employer.
                shared = value.get('views', {})
                if not isinstance(shared, dict) or len(shared) > len(jobs or []) or not all(
                        isinstance(k, str) and 0 < len(k) <= 64 for k in shared):
                    raise ValueError('invalid presentation')
                def bilingual(v):
                    return isinstance(v, dict) and set(v) == {'zh', 'en'} and all(_valid_view(x) for x in v.values())
                allowed = {j['uid'] for j in jobs or []}
                if set(value['results']) != allowed or not all(bilingual(v) for v in shared.values()) or not all(
                        (isinstance(v, str) and v in shared) or bilingual(v) for v in value['results'].values()):
                    raise ValueError('invalid presentation')
                if len(json.dumps(value, ensure_ascii=False).encode('utf-8')) > MAX_RESULT:
                    raise ValueError('serialized result too large')
                if _latest(config) != analysis_id:
                    raise ValueError('analysis changed')
                atomic_json(directory / 'results.json', value, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            _log(config, plugin_id, hook, 'Timeout', 'Plugin exceeded its execution time budget.')
        except WorkerError as exc:
            _log(config, plugin_id, hook, exc.code, exc.message)
        except (Exception,) as exc:
            from . import RunTimeout
            if isinstance(exc, RunTimeout):
                raise
            _log(config, plugin_id, hook, type(exc).__name__)


def _configured(config):
    plugins = config.get('plugins')
    return ('demo' not in config and isinstance(plugins, dict)
            and any(not isinstance(value, dict) or value.get('enabled', False) is not False
                    for value in plugins.values()))


def dispatch(config: dict, hook: str, jobs: list | None = None, analysis_id: int | None = None) -> None:
    try:
        _dispatch(config, hook, jobs, analysis_id)
    except RunTimeout:
        raise
    except Exception as exc:
        for plugin_id in config.get('plugins', {}) if isinstance(config.get('plugins'), dict) else ():
            if isinstance(plugin_id, str) and _ID.fullmatch(plugin_id):
                _log(config, plugin_id, hook, type(exc).__name__)


def _analysis_jobs(conn):
    return [dict(row) for row in conn.execute(
        "SELECT uid, company, title, source, listing_date, first_seen_at, last_seen_at, dedupe_group "
        "FROM jobs ORDER BY uid")]


def after_analyze(config, conn):
    """Optional preparation after durable core success, with no disabled-path query."""
    if not _configured(config):
        return
    items = []
    try:
        items = list_plugins(config)
        if not any(item['status'] == 'enabled' for item in items):
            # Still record invalid configured plugins through the ordinary path.
            dispatch(config, 'after_analyze_success')
            return
        analysis_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        jobs = _analysis_jobs(conn)
        dispatch(config, 'after_analyze_success', jobs, analysis_id)
    except RunTimeout:
        raise
    except Exception as exc:
        for item in items:
            if item['enabled']:
                _log(config, item['id'], 'after_analyze_success', type(exc).__name__)
