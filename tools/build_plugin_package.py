#!/usr/bin/env python3
"""Build a deterministic plugin package (.zip) and print its SHA-256.

    python tools/build_plugin_package.py PLUGIN_DIR [-o OUT.zip]

The manifest is checked with the host's own validator first. Entries are
sorted, timestamps fixed, modes normalised (0644 files, 0755 directories),
and `__pycache__`, dotfiles and symlinks are left out, so the same directory
always produces the same bytes and the same SHA-256. The result is checked
with the installer's archive validation before it is reported.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from jobs import plugin_host, plugin_install  # noqa: E402

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class BuildError(Exception):
    pass


def _members(directory):
    """Relative POSIX paths of regular files to package, sorted."""
    found = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__'
                         and not (base / d).is_symlink())
        for name in sorted(files):
            path = base / name
            if name.startswith('.') or path.is_symlink():
                continue
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            found.append(path.relative_to(directory).as_posix())
    return sorted(found)


def build(directory, output=None):
    """Write the package and return (path, size, sha256)."""
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise BuildError(f'{directory} is not a directory.')
    try:
        manifest = plugin_host._manifest(directory, directory.name)
    except (OSError, ValueError, TypeError) as exc:
        raise BuildError(f'The manifest is not valid for this host ({exc}); '
                         'the directory name must equal the manifest id.') from None
    output = Path(output) if output else Path.cwd() / f"{manifest['id']}-{manifest['version']}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in _members(directory):
            info = zipfile.ZipInfo(relative, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, (directory / relative).read_bytes())
    data = buffer.getvalue()
    try:
        plugin_install.inspect(data)
    except plugin_install.InstallError as exc:
        raise BuildError('The package would be refused: ' + exc.message) from None
    output.write_bytes(data)
    return output, len(data), hashlib.sha256(data).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('plugin_dir')
    parser.add_argument('-o', '--output', help='output .zip path (default: <id>-<version>.zip here)')
    parser.add_argument('--json', action='store_true', help='print {path, size, sha256} as JSON')
    args = parser.parse_args(argv)
    try:
        path, size, digest = build(args.plugin_dir, args.output)
    except (BuildError, OSError) as exc:
        print(str(exc) if isinstance(exc, BuildError) else f'Writing the package failed: {exc}', file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({'path': str(path), 'size': size, 'sha256': digest}))
    else:
        print(f'{path}\n{size} bytes\nsha256 {digest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
