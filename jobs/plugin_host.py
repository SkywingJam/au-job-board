"""Read-only discovery and isolated execution of optional local plugins.

Installed plugins are trusted code, not sandboxed. The process boundary contains
accidental crashes and hangs; it is not a security boundary against hostile code.
"""
from __future__ import annotations

import json
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
    if value.get('compatibility') != {'host_api': 1}:
        raise ValueError('incompatible host API')
    if value.get('entry') != 'plugin.py':
        raise ValueError('invalid entry')
    if not isinstance(value.get('hooks'), list) or not set(value['hooks']) <= {'after_fetch', 'after_analyze_success'} or any(not isinstance(x, str) for x in value['hooks']):
        raise ValueError('invalid hooks')
    if value.get('network') not in ('after_fetch', 'none') or not isinstance(value.get('display'), bool) or not isinstance(value.get('i18n'), list) or not set(value['i18n']) <= {'zh', 'en'}:
        raise ValueError('invalid capabilities')
    return value


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
            item['status'] = 'enabled' if item['enabled'] else 'disabled'
        except RunTimeout:
            raise
        except Exception:
            item['status'] = 'error'
        result.append(item)
    return result


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
                for uid in uids:
                    entry = value['results'].get(uid, {})
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
    payload = json.dumps(request, ensure_ascii=False).encode('utf-8')
    if len(payload) > MAX_REQUEST:
        raise ValueError('request too large')
    with tempfile.TemporaryDirectory(prefix='jobs-plugin-') as tmp:
        request_path, result_path = Path(tmp) / 'input.json', Path(tmp) / 'output.json'
        request_path.write_bytes(payload)
        request_path.chmod(0o600)
        command = [sys.executable, '-m', 'jobs.plugin_worker', str(PLUGIN_ROOT / plugin_id / 'plugin.py'), hook, str(request_path), str(result_path)]
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
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
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
            if hook not in manifest['hooks']:
                continue
            directory = _root(config) / plugin_id
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            request = {'api': 1, 'hook': hook, 'data_dir': str(directory / 'data'),
                       'config': config.get('plugins', {}).get(plugin_id, {}), 'jobs': jobs or [], 'analysis_id': analysis_id,
                       'allow_network': hook == 'after_fetch' and manifest['network'] == 'after_fetch'}
            value = _run_worker(config, plugin_id, hook, request)
            if hook == 'after_fetch' and isinstance(value, dict) and value.get('errors'):
                _log(config, plugin_id, hook, 'RefreshError')
            if hook == 'after_analyze_success':
                if not isinstance(value, dict) or set(value) != {'analysis_id', 'results'} or value['analysis_id'] != analysis_id or not isinstance(value['results'], dict):
                    raise ValueError('invalid result')
                allowed = {j['uid'] for j in jobs or []}
                if set(value['results']) != allowed or not all(isinstance(v, dict) and set(v) == {'zh', 'en'} and all(_valid_view(x) for x in v.values()) for v in value['results'].values()):
                    raise ValueError('invalid presentation')
                if len(json.dumps(value).encode('utf-8')) > MAX_RESULT:
                    raise ValueError('serialized result too large')
                if _latest(config) != analysis_id:
                    raise ValueError('analysis changed')
                atomic_json(directory / 'results.json', value)
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
