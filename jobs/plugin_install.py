"""Install, upgrade and uninstall plugin packages from a local CLI.

A package is one `.zip` whose root holds `manifest.json` (one wrapping top-level
directory is also accepted). Plugins are trusted code, but the archive is
treated as hostile input: every entry is checked, fully read and verified in
memory before anything is written, and files are created one by one with
private modes (never `ZipFile.extractall`, never archive permission bits).

Installing never enables or grants a plugin. The caller (the CLI) holds the
pipeline's exclusive run lock around `install` and `uninstall`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from . import RunTimeout, __version__, plugin_host
from .run_state import _retry_sharing, atomic_json

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_ENTRIES = 4000
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_REDIRECTS = 5
DOWNLOAD_TIMEOUT = 60
MAX_PATH = 512
MAX_DEPTH = 16
USER_AGENT = 'au-job-board/' + __version__
_CHUNK = 1024 * 1024
_SHA256 = re.compile(r'[0-9a-fA-F]{64}')
# Conservative portable names: no leading dot, no spaces, colons or backslashes.
_SEGMENT = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._-]{0,254}')
_WINDOWS_DEVICES = re.compile(r'(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?', re.IGNORECASE)


class InstallError(Exception):
    """An expected refusal; `message` is one sentence for the user."""
    def __init__(self, message):
        super().__init__(message)
        self.message = message


@dataclass
class Archive:
    data: bytes
    sha256: str
    source: str          # recorded provenance: a basename or a credential-free URL


@dataclass
class Package:
    manifest: dict
    prefix: str                              # stripped wrapping directory, '' or 'name/'
    files: list = field(default_factory=list)  # [(relative path, ZipInfo)]
    dirs: list = field(default_factory=list)   # [relative path]

    @property
    def plugin_id(self):
        return self.manifest['id']


# ---------------------------------------------------------------- obtaining bytes

_HIDDEN = re.compile('[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]')


def visible(text):
    """Text that is safe to print in a terminal: control characters, newlines and
    direction overrides are shown as escapes so a manifest or record cannot hide
    or forge lines of the review."""
    return _HIDDEN.sub(lambda m: '\\x%02x' % ord(m.group()) if ord(m.group()) < 256 else '\\u%04x' % ord(m.group()),
                       str(text))


def normalize_sha256(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value.strip()):
        raise InstallError('The --sha256 value must be 64 hexadecimal characters.')
    return value.strip().lower()


def is_url(source):
    return isinstance(source, str) and '://' in source


def _check_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise InstallError('The download address is not a valid URL.') from None
    if parts.scheme.lower() != 'https':
        raise InstallError('Only https:// download addresses are accepted.')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise InstallError('The download address must not contain a user name or password.')
    if not parts.hostname or port == 0:
        raise InstallError('The download address has no valid host.')
    return parts


def recorded_url(url):
    """The URL as given, without query or fragment, which may carry tokens."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))


class _HttpsRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _check_url(newurl)
        except InstallError:
            raise InstallError('The download redirected to an address that is not a plain https:// URL.') from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)

    def http_error_302(self, req, fp, code, msg, headers):
        # The standard handler counts only loops of the same URL against its
        # own limit; keep the documented maximum explicit.
        visited = getattr(req, 'redirect_dict', {})
        if sum(visited.values()) >= MAX_REDIRECTS:
            raise InstallError(f'The download redirected more than {MAX_REDIRECTS} times.')
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def download(url, *, handlers=(), clock=time.monotonic):
    """Bytes of an https URL, bounded in size and time. `handlers` lets tests
    replace the network (an `HTTPSHandler` subclass) without touching sockets."""
    _check_url(url)
    opener = urllib.request.build_opener(_HttpsRedirects(), *handlers)
    # The fragment is never sent; only the application and version identify us.
    request = urllib.request.Request(urllib.parse.urldefrag(url)[0], headers={'User-Agent': USER_AGENT})
    deadline = clock() + DOWNLOAD_TIMEOUT
    chunks, size = [], 0

    def on_time():
        # One budget for connecting, redirects and the whole body. Each socket
        # operation also has its own timeout, so a stalled peer cannot hold on.
        if clock() > deadline:
            raise InstallError(f'The download took longer than {DOWNLOAD_TIMEOUT} seconds.')
    try:
        with opener.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            on_time()
            final = response.geturl() if hasattr(response, 'geturl') else url
            _check_url(final)
            length = response.headers.get('Content-Length') if getattr(response, 'headers', None) else None
            if length is not None and length.strip().isdigit() and int(length) > MAX_ARCHIVE:
                raise InstallError('The download is larger than the 128 MiB package limit.')
            # read1 returns after one underlying read, so a slow trickle is
            # checked against the deadline between reads instead of blocking.
            read = getattr(response, 'read1', None) or response.read
            while True:
                chunk = read(min(_CHUNK, MAX_ARCHIVE + 1 - size))
                on_time()
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise InstallError('The download is larger than the 128 MiB package limit.')
    except (InstallError, RunTimeout):
        raise
    except urllib.error.HTTPError as exc:
        raise InstallError(f'The download failed with HTTP status {exc.code}.') from None
    except (OSError, ValueError, http.client.HTTPException) as exc:   # URLError is an OSError
        raise InstallError('The download failed before it completed (' + type(exc).__name__ + ').') from None
    return b''.join(chunks)


def read_local(path):
    path = Path(path)
    try:
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
    except OSError:
        raise InstallError(f'Cannot read the package file {path.name}.') from None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise InstallError('The package must be a regular .zip file.')
        if info.st_size > MAX_ARCHIVE:
            raise InstallError('The package is larger than the 128 MiB limit.')
        data = stream.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE:
        raise InstallError('The package is larger than the 128 MiB limit.')
    return data


def obtain(source, expected_sha256=None, *, handlers=()):
    """Read a local `.zip` or download an https URL, then check its SHA-256
    before the archive is opened. A URL needs `expected_sha256`."""
    expected = normalize_sha256(expected_sha256)
    if is_url(source):
        _check_url(source)
        if expected is None:
            raise InstallError('A download needs --sha256 with the published SHA-256 of the package.')
        data, label = download(source, handlers=handlers), recorded_url(source)
    else:
        data, label = read_local(source), Path(source).name
    digest = hashlib.sha256(data).hexdigest()
    if expected is not None and not hmac.compare_digest(digest, expected):
        raise InstallError(f'The package SHA-256 is {digest}, not the expected value; nothing was installed.')
    return Archive(data=data, sha256=digest, source=label[:2048])


# ---------------------------------------------------------------- validation

def _entry_name(info):
    name = info.orig_filename
    if not isinstance(name, str) or not name or '\x00' in name or '\\' in name:
        raise InstallError('The package contains an entry with an unsafe name.')
    if name.startswith('/') or re.match(r'[A-Za-z]:', name):
        raise InstallError('The package contains an absolute path.')
    directory = name.endswith('/')
    parts = name[:-1].split('/') if directory else name.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise InstallError('The package contains a path with an empty, "." or ".." part.')
    if len(name) > MAX_PATH or len(parts) > MAX_DEPTH:
        raise InstallError('The package contains a path that is too long or too deep.')
    for part in parts:
        if (not _SEGMENT.fullmatch(part) or part.endswith('.')
                or _WINDOWS_DEVICES.fullmatch(part)):
            raise InstallError('The package contains a file name outside letters, digits, ".", "_" and "-".')
    return '/'.join(parts), directory


def _entry_kind(info, directory):
    if info.flag_bits & 0x1:
        raise InstallError('The package contains an encrypted entry.')
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise InstallError('The package uses a compression method other than stored or deflate.')
    mode = info.external_attr >> 16
    if info.create_system == 3 and mode:
        kind = stat.S_IFMT(mode) or (stat.S_IFDIR if directory else stat.S_IFREG)   # permission bits only
        if kind == stat.S_IFLNK:
            raise InstallError('The package contains a symbolic link.')
        if kind not in (stat.S_IFREG, stat.S_IFDIR) or (kind == stat.S_IFDIR) != directory:
            raise InstallError('The package contains an entry that is not a regular file or directory.')
    if info.external_attr & 0x400:      # MS-DOS reparse point
        raise InstallError('The package contains a symbolic link.')
    if directory and info.file_size:
        raise InstallError('The package contains a directory entry with data.')


def _read_entry(archive, info):
    """The entry's bytes, read with a hard bound; the CRC is verified at EOF."""
    chunks, size = [], 0
    with archive.open(info) as stream:
        while True:
            chunk = stream.read(min(_CHUNK, MAX_FILE + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE:
                raise InstallError('A file in the package is larger than the 64 MiB limit.')
            chunks.append(chunk)
    if size != info.file_size:
        raise InstallError('A file in the package does not match its recorded size.')
    return b''.join(chunks)


def _check_directory_size(data):
    """Walk the central directory the way zipfile will and stop at the entry
    limit, before zipfile allocates an object per record."""
    import struct
    bad = InstallError('The package is not a valid .zip file.')
    end = -1
    if len(data) >= 22 and data[-22:-18] == b'PK\x05\x06' and data[-2:] == b'\x00\x00':
        end = len(data) - 22
    else:
        end = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        raise bad
    size, offset = struct.unpack_from('<LL', data, end + 12)
    stop = end
    if end >= 20 and data[end - 20:end - 16] == b'PK\x06\x07':          # zip64 locator
        where = struct.unpack_from('<Q', data, end - 12)[0]
        if where + 56 > len(data) or data[where:where + 4] != b'PK\x06\x06':
            raise bad
        size, offset = struct.unpack_from('<QQ', data, where + 40)
        stop = where
    start = stop - size
    if size < 0 or start < 0:
        raise bad
    position, count = start, 0
    while position < stop:
        if position + 46 > stop or data[position:position + 4] != b'PK\x01\x02':
            raise bad
        count += 1
        if count > MAX_ENTRIES:
            raise InstallError(f'The package has more than {MAX_ENTRIES} entries.')
        name, extra, comment = struct.unpack_from('<HHH', data, position + 28)
        position += 46 + name + extra + comment


def _open_archive(data):
    if len(data) > MAX_ARCHIVE:
        raise InstallError('The package is larger than the 128 MiB limit.')
    _check_directory_size(data)
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except RunTimeout:
        raise
    except Exception:       # BadZipFile, struct.error, UnicodeDecodeError, ...
        raise InstallError('The package is not a valid .zip file.') from None


def inspect(data):
    """Validate the archive and its manifest; nothing is written anywhere.
    Returns a `Package` whose entries were all read and CRC-checked."""
    with _open_archive(data) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise InstallError(f'The package has more than {MAX_ENTRIES} entries.')
        entries, folded, total = [], {}, 0
        for info in infos:
            name, directory = _entry_name(info)
            _entry_kind(info, directory)
            key = name.casefold()
            if key in folded:
                raise InstallError('The package contains two entries whose names differ only by case, or a duplicate entry.')
            folded[key] = directory
            if not directory:
                if info.file_size > MAX_FILE:
                    raise InstallError('A file in the package is larger than the 64 MiB limit.')
                total += info.file_size
                if total > MAX_TOTAL:
                    raise InstallError('The package unpacks to more than 256 MiB.')
            entries.append((name, directory, info))
        # A file may not also be used as a directory (a.txt and a.txt/b).
        for name, _, _ in entries:
            parts = name.casefold().split('/')
            for depth in range(1, len(parts)):
                if folded.get('/'.join(parts[:depth])) is False:
                    raise InstallError('The package uses a file name as a directory.')
        prefix = _prefix(entries)
        files, dirs, manifest_bytes = [], [], None
        try:
            for name, directory, info in entries:
                relative = name[len(prefix):]
                if not relative:
                    continue
                if directory:
                    dirs.append(relative)
                    continue
                content = _read_entry(archive, info)     # bombs and bad CRCs stop here
                if relative == 'manifest.json':
                    manifest_bytes = content
                files.append((relative, info))
        except (InstallError, RunTimeout):
            raise
        except Exception:
            # BadZipFile (CRC or header mismatch), zlib.error, EOFError and the like.
            raise InstallError('The package is damaged: a file failed its integrity check.') from None
    manifest = _package_manifest(manifest_bytes)
    names = {relative for relative, _ in files}
    for entry in _entries(manifest):
        if entry not in names:
            raise InstallError(f'The package does not contain {entry}, which its manifest names.')
    return Package(manifest=manifest, prefix=prefix, files=files, dirs=dirs)


def _prefix(entries):
    names = {name for name, directory, _ in entries if not directory}
    if 'manifest.json' in names:
        return ''
    tops = {name.split('/', 1)[0] for name, _, _ in entries}
    if len(tops) == 1:
        top = next(iter(tops))
        if top + '/manifest.json' in names:
            return top + '/'
    raise InstallError('The package has no manifest.json at its root.')


def _package_manifest(raw):
    if raw is None:
        raise InstallError('The package has no manifest.json at its root.')
    if len(raw) > plugin_host.MAX_MANIFEST:
        raise InstallError('The package manifest is larger than 32 KiB.')
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeError, ValueError):
        raise InstallError('The package manifest is not valid JSON.') from None
    plugin_id = value.get('id') if isinstance(value, dict) else None
    if not isinstance(plugin_id, str) or not plugin_host._ID.fullmatch(plugin_id):
        raise InstallError('The package manifest id must be lowercase letters, digits, "_" or "-", starting with a letter.')
    for key in ('name', 'description', 'version'):
        texts = value.get(key)
        texts = list(texts.values()) if isinstance(texts, dict) else [texts]
        if any(isinstance(text, str) and visible(text) != text for text in texts):
            raise InstallError(f'The package manifest {key} contains control or direction characters.')
    try:
        return plugin_host.validate_manifest(value, plugin_id)
    except RunTimeout:
        raise
    except Exception:
        raise InstallError('The package manifest is not valid for this host (unknown fields, permissions or compatibility).') from None


def _entries(manifest):
    found = {manifest['entry']}
    for slot in ('page', 'sidebar'):
        if isinstance(manifest.get(slot), dict):
            found.add(manifest[slot]['entry'])
    return sorted(found)


def check_installable(config, package, *, upgrade):
    """Refusals that depend on this instance: bundled id, state, compatibility."""
    plugin_id = package.plugin_id
    reason = plugin_host.incompatibility(package.manifest)
    if reason is not None:
        raise InstallError(f'This package requires board version {reason["requires"]}, '
                           f'but this board is version {reason["core"]}.')
    if plugin_id in plugin_host.RESERVED_IDS or plugin_id in plugin_host.bundled_ids():
        raise InstallError(f'"{plugin_id}" is a plugin bundled with the board and cannot be installed or replaced.')
    present = installed(config, plugin_id)
    if upgrade and not present:
        raise InstallError(f'"{plugin_id}" is not installed; use "plugin install" instead.')
    if not upgrade and present:
        raise InstallError(f'"{plugin_id}" is already installed; use "plugin upgrade" to replace it.')
    if not upgrade and _count(config) >= plugin_host.MAX_PLUGINS:
        raise InstallError(f'The board already has {plugin_host.MAX_PLUGINS} plugins, the maximum.')


def _count(config):
    return len(plugin_host.plugin_dirs(config))


# ---------------------------------------------------------------- the plugin location

def package_dir(config, plugin_id):
    return plugin_host.packages_root(config) / plugin_id


def record_path(config, plugin_id):
    return plugin_host.packages_root(config) / (plugin_id + '.install.json')


def installed(config, plugin_id):
    """True when the package is present, or set aside by an interrupted upgrade
    (`recover` puts it back under the lock)."""
    path = package_dir(config, plugin_id)
    if path.is_symlink() or path.exists():
        return True
    pattern = re.compile(r'\.old-' + re.escape(plugin_id) + r'-[0-9a-f]{32}')
    try:
        return any(pattern.fullmatch(p.name) for p in path.parent.iterdir())
    except OSError:
        return False


def checked_root(config, *, create=False):
    """The packages root, refused when it is a symbolic link or not a directory.
    Every operation that renames, deletes or writes goes through this first."""
    root = plugin_host.packages_root(config)
    try:
        if root.is_symlink():
            raise InstallError('The plugin package directory is a symbolic link; refusing to change anything through it.')
        if root.exists() and not root.is_dir():
            raise InstallError('The plugin package location exists and is not a directory.')
        if create and not root.exists():
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if create and os.name != 'nt':
            os.chmod(root, 0o700)
    except OSError as exc:
        raise InstallError('The plugin package directory could not be prepared (' + type(exc).__name__ + ').') from None
    return root


def _prepare_root(config):
    return checked_root(config, create=True)


def _rename(source, target):
    # Windows refuses to rename a directory while a file in it is briefly open.
    _retry_sharing(lambda: os.rename(source, target), 2.0)


def _remove_tree(path):
    def retry(function, name, _error):
        # A read-only entry (Windows) gets one more attempt after chmod.
        try:
            os.chmod(name, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(name) else 0))
            function(name)
        except OSError:
            pass
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path, onexc=retry)


def recover(config):
    """Finish an operation a crash interrupted. Caller holds the run lock.

    `.old-<id>-*` is the previous package while an upgrade swaps it: put it
    back when `<id>` is missing, otherwise drop it. Staging and removed
    directories are always dropped."""
    root = checked_root(config)
    if not root.is_dir():
        return
    try:
        for path in sorted(root.iterdir()):
            name = path.name
            if path.is_symlink() or not name.startswith('.'):
                continue
            old = re.fullmatch(r'\.old-([a-z][a-z0-9_-]{0,63})-[0-9a-f]{32}', name)
            if old and path.is_dir():
                target, kept = root / old.group(1), root / (name + '.json')
                record = record_path(config, old.group(1))
                if not target.exists() and not target.is_symlink():
                    # The swap never completed: the previous package and its record come back.
                    _rename(path, target)
                    record.unlink(missing_ok=True)
                    if kept.is_file() and not kept.is_symlink():
                        _rename(kept, record)
                else:
                    # The new package is in place. Its record is only trusted when the
                    # previous one was already retired by a completed write.
                    _remove_tree(path)
                    if kept.exists() or kept.is_symlink():
                        kept.unlink()
            elif re.fullmatch(r'\.(staging|removed-[a-z][a-z0-9_-]{0,63})-[0-9a-f]{32}', name) and path.is_dir():
                _remove_tree(path)
        for path in sorted(root.iterdir()):          # a record backup whose package backup is gone
            if (re.fullmatch(r'\.old-[a-z][a-z0-9_-]{0,63}-[0-9a-f]{32}\.json', path.name)
                    and not (root / path.name[:-5]).exists()):
                path.unlink()
    except OSError as exc:
        raise InstallError('An interrupted plugin change could not be cleaned up (' + type(exc).__name__ + ').') from None


def _write_file(path, content):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _extract(data, package, target):
    """Create every directory (0700) and file (0600) under `target` one by one."""
    target.mkdir(mode=0o700)
    with _open_archive(data) as archive:
        for relative in sorted(set(package.dirs) | {r.rsplit('/', 1)[0] for r, _ in package.files if '/' in r}):
            current = target
            for part in relative.split('/'):
                current = current / part
                if current.is_symlink():
                    raise InstallError('The staging directory changed while installing.')
                if not current.exists():
                    current.mkdir(mode=0o700)
        for relative, info in package.files:
            _write_file(target.joinpath(*relative.split('/')), _read_entry(archive, info))


def write_record(config, package, archive):
    record = {'id': package.plugin_id, 'version': package.manifest['version'], 'sha256': archive.sha256,
              'source': archive.source, 'installed_at': datetime.now(timezone.utc).isoformat()}
    atomic_json(record_path(config, package.plugin_id), record, ensure_ascii=False)
    return record


def install(config, archive, package, *, upgrade=False):
    """Place a validated package. Caller holds the run lock and confirmed.

    The package is extracted into a staging directory and renamed into
    place. An upgrade keeps the previous directory until the new one is in
    place and the record is written, and restores it on any failure."""
    check_installable(config, package, upgrade=upgrade)
    root = _prepare_root(config)
    token = uuid.uuid4().hex
    staging = root / ('.staging-' + token)
    target = root / package.plugin_id
    backup = root / ('.old-' + package.plugin_id + '-' + token)
    record_file = record_path(config, package.plugin_id)
    record_backup = root / (backup.name + '.json')
    restored = True
    placed = False          # the new package sits at `target`

    def roll_back():
        # Undo in reverse order whatever happened; each step checks the disk.
        nonlocal restored, placed
        restored = False
        if placed:
            record_file.unlink(missing_ok=True)
            if target.exists() and not target.is_symlink():
                _rename(target, staging)
            placed = False
        if backup.exists() and not target.exists():
            _rename(backup, target)
        if record_backup.exists() and not record_file.exists():
            _rename(record_backup, record_file)
        restored = True
    try:
        _extract(archive.data, package, staging)
        try:
            if upgrade:
                # The old record leaves with the old package, so a crash can never
                # show the previous version and hash for new code.
                if record_file.is_file() and not record_file.is_symlink():
                    _rename(record_file, record_backup)
                _rename(target, backup)
            _rename(staging, target)
            placed = True
            record = write_record(config, package, archive)
            plugin_host._sync_directory(root)
        except BaseException:
            roll_back()
            raise
    except (InstallError, RunTimeout):
        raise
    except OSError as exc:
        state = ('the previous state was kept' if restored else
                 'the previous state could not be fully restored; run the command again to repair it')
        raise InstallError('Writing the plugin package failed (' + type(exc).__name__ + '); ' + state + '.') from None
    finally:
        try:
            _remove_tree(staging)
        except OSError:
            pass
    for leftover in (backup, record_backup):
        try:
            if leftover.is_dir() and not leftover.is_symlink():
                _remove_tree(leftover)
            else:
                leftover.unlink(missing_ok=True)
        except OSError:
            pass        # an `.old-*` leftover is ignored and cleaned by the next operation
    return record


def is_bundled(plugin_id):
    return plugin_id in plugin_host.RESERVED_IDS or plugin_id in plugin_host.bundled_ids()


def uninstall(config, plugin_id, *, stale=False):
    """Remove an installed package and its record; plugin data stays.
    Caller holds the run lock and already disabled and revoked the plugin.
    `stale` removes an ignored installed copy whose id a later release bundles."""
    if not isinstance(plugin_id, str) or not plugin_host._ID.fullmatch(plugin_id):
        raise InstallError('Plugin ids are lowercase letters, digits, "_" or "-", starting with a letter.')
    if not stale and is_bundled(plugin_id):
        raise InstallError(f'"{plugin_id}" is bundled with the board and cannot be uninstalled; disable it in Settings > Plugins instead.')
    root = checked_root(config)
    target, record = root / plugin_id, record_path(config, plugin_id)
    if not installed(config, plugin_id) and not record.exists():
        raise InstallError(f'"{plugin_id}" is not an installed plugin.')
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            removed = root / ('.removed-' + plugin_id + '-' + uuid.uuid4().hex)
            _rename(target, removed)
            _remove_tree(removed)
        # A backup an earlier upgrade could not delete must never come back.
        backup = re.compile(r'\.old-' + re.escape(plugin_id) + r'-[0-9a-f]{32}')
        for path in list(root.iterdir()) if root.is_dir() else []:
            if backup.fullmatch(path.name) and not path.is_symlink():
                removed = root / ('.removed-' + plugin_id + '-' + uuid.uuid4().hex)
                _rename(path, removed)
                _remove_tree(removed)
            elif path.name.endswith('.json') and backup.fullmatch(path.name[:-5]):
                path.unlink()
        record.unlink(missing_ok=True)
        if root.is_dir():
            plugin_host._sync_directory(root)
    except OSError as exc:
        raise InstallError('Removing the plugin package failed (' + type(exc).__name__ + ').') from None


# ---------------------------------------------------------------- review text

def profile_summary(manifest):
    """(profile in plain words, runs Python on this machine)."""
    if plugin_host.host_api(manifest) == 1:
        network = ('the host downloads public reference lists after a fetch'
                   if manifest['permissions']['network'] else 'no network access')
        return ('Python hooks: runs the package\'s Python code on this machine in a supervised child process '
                f'after fetch/analysis, reading job IDs and company names; {network}.'), True
    if plugin_host.handed(manifest):
        detail = ', including descriptions' if 'job_detail' in manifest['permissions'] else ''
        return ('Handed-jobs sidebar and page: browser frames that read only the jobs you hand to them'
                f'{detail}; no network access, no files.'), False
    return ('Map page: a browser frame that reads summaries of the current job list and loads map tiles '
            'from tiles.openfreemap.org.'), False
