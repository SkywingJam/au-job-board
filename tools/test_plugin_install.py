#!/usr/bin/env python3
"""Synthetic, offline checks for plugin packages: install, upgrade, uninstall,
the plugin location resolver, `compatibility.core` and the list status fields.

Every plugin here is made up (`demo-sidebar`, `demo-hook`, `demo-bundled`) and
built in a temporary directory; no network is used.
"""
from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.response
import warnings
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import build_plugin_package  # noqa: E402
import temp_cleanup  # noqa: E402
from jobs import (__version__, agent_api, cli, db, panel, plugin_host as host,  # noqa: E402
                  plugin_install as install, plugin_pages, run_state)
from jobs.config_edit import ConfigEditor, ConfigEditError  # noqa: E402

WORKSPACE = json.loads((ROOT / 'plugins/workspace/manifest.json').read_text())
HOOK = {'id': 'demo-hook', 'name': {'en': 'Demo hook', 'zh': '示例'}, 'version': '1.0.0',
        'compatibility': {'host_api': 1}, 'entry': 'plugin.py', 'hooks': ['after_analyze_success'],
        'network': 'none', 'display': True, 'i18n': ['en', 'zh'],
        'permissions': {'job_fields': ['uid', 'company'], 'files': ['plugin_data'], 'network': [],
                        'execution': ['python_worker'], 'display': ['job_badges', 'job_details']}}
HOOK_CODE = ("def after_analyze_success(r):\n"
             " v={'badges':[{'text':'hook','level':'info','tooltip':'t'}],'sections':[]}\n"
             " return {'analysis_id':r['analysis_id'],'results':{j['uid']:{'zh':v,'en':v} for j in r['jobs']}}\n")


def sidebar_manifest(plugin_id='demo-sidebar', version='1.0.0', **compat):
    manifest = copy.deepcopy(WORKSPACE)
    manifest.update(id=plugin_id, version=version)
    manifest['sidebar']['id'] = manifest['page']['id'] = plugin_id
    manifest['compatibility'] = {'host_api': 2, **compat}
    return manifest


def hook_manifest(plugin_id='demo-hook', version='1.0.0', **compat):
    manifest = copy.deepcopy(HOOK)
    manifest.update(id=plugin_id, version=version)
    manifest['compatibility'] = {'host_api': 1, **compat}
    return manifest


def write_plugin(directory, manifest, code=HOOK_CODE, extra=None):
    """A plugin source directory; returns its path."""
    target = Path(directory) / manifest['id']
    target.mkdir(parents=True, exist_ok=True)
    (target / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    if host.host_api(manifest) == 1:
        (target / 'plugin.py').write_text(code, encoding='utf-8')
    else:
        (target / 'web').mkdir(exist_ok=True)
        (target / 'web/index.html').write_text('<p>synthetic sidebar ' + manifest['version'] + '</p>')
        (target / 'web/compare.html').write_text('<p>synthetic compare</p>')
        (target / 'web/app.js').write_text('// synthetic asset')
    for name, content in (extra or {}).items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(content, encoding='utf-8')
    return target


def raw_zip(entries):
    """entries: [(name, bytes, mode or None)] written as Unix entries, unchecked."""
    buffer = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(buffer, 'w') as archive:
        warnings.simplefilter('ignore')       # duplicate names are written on purpose
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (mode if mode is not None else stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def patch_central(data, name, *, flags=None, size=None):
    """Rewrite flag bits / uncompressed size of one entry in both headers."""
    data = bytearray(data)
    encoded = name.encode()
    position = 0
    while (position := data.find(b'PK\x01\x02', position)) >= 0:
        length = struct.unpack_from('<H', data, position + 28)[0]
        if bytes(data[position + 46:position + 46 + length]) == encoded:
            local = struct.unpack_from('<I', data, position + 42)[0]
            if flags is not None:
                struct.pack_into('<H', data, position + 8, flags)
                struct.pack_into('<H', data, local + 6, flags)
            if size is not None:
                struct.pack_into('<I', data, position + 24, size)
                struct.pack_into('<I', data, local + 22, size)
        position += 4
    return bytes(data)


class FakeHTTPS(urllib.request.HTTPSHandler):
    """Replaces the network: {url: (status, headers, body)}."""
    def __init__(self, routes):
        super().__init__()
        self.routes, self.seen = routes, []

    def https_open(self, request):
        self.seen.append((request.full_url, dict(request.header_items())))
        status, headers, body = self.routes[request.full_url]
        message = http.client.HTTPMessage()
        for key, value in headers.items():
            message[key] = value
        response = urllib.response.addinfourl(io.BytesIO(body), message, request.full_url, status)
        response.msg = 'OK' if status == 200 else 'Redirect'
        return response


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(temp_cleanup.cleanup, self.tmp)
        self.root = Path(self.tmp.name)
        self.bundled = self.root / 'bundled'
        self.bundled.mkdir()
        write_plugin(self.bundled, sidebar_manifest('demo-bundled'))
        patcher = patch.object(host, 'PLUGIN_ROOT', self.bundled)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sources = self.root / 'sources'
        self.packages = self.root / 'packages'
        self.packages.mkdir()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.config_path = self.root / 'config.yaml'
        self.config = {'db_path': str(self.data / 'jobs.db'), 'plugins': {}}
        self.write_config()

    def write_config(self, plugins=None):
        self.config['plugins'] = plugins or {}
        text = f'db_path: {json.dumps(self.config["db_path"])}\n'
        if plugins:
            text += 'plugins:\n' + ''.join(f'  {k}:\n    enabled: {str(v["enabled"]).lower()}\n'
                                           for k, v in plugins.items())
        self.config_path.write_text(text, encoding='utf-8')

    def build(self, manifest, name=None, **kwargs):
        source = write_plugin(self.root / ('src-' + (name or manifest['id'] + manifest['version'])), manifest, **kwargs)
        out = self.packages / ((name or manifest['id'] + '-' + manifest['version']) + '.zip')
        path, _, digest = build_plugin_package.build(source, out)
        return path, digest

    def build_unchecked(self, manifest):
        """A package the builder would refuse: the manifest goes in as written."""
        source = write_plugin(self.root / ('raw-' + manifest['id']), manifest)
        out = self.packages / (manifest['id'] + '-raw.zip')
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
        return out, hashlib.sha256(out.read_bytes()).hexdigest()

    def cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        stream = stdin if stdin is not None else io.StringIO('')
        with patch.object(sys, 'stdin', stream), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['--config', str(self.config_path), 'plugin', *argv])
        return code, out.getvalue(), err.getvalue()

    def installed_root(self):
        return host.packages_root(self.config)

    def landed(self):
        """Plugin directories and records in the installed location (no leftovers)."""
        root = self.installed_root()
        return sorted(p.name for p in root.iterdir()) if root.exists() else []

    def item(self, plugin_id):
        # The saved configuration, as the panel and CLI read it.
        current = ConfigEditor(self.config, self.config_path).plugin_config()
        return next((i for i in host.list_plugins(current) if i['id'] == plugin_id), None)


class Packaging(Base):
    def test_builder_is_deterministic_and_skips_dotfiles_caches_and_links(self):
        source = write_plugin(self.root / 'src', hook_manifest(), extra={
            '.hidden': 'x', '__pycache__/plugin.cpython-312.pyc': 'x', 'lib/helper.py': 'X = 1\n'})
        if hasattr(os, 'symlink'):
            try:
                os.symlink(self.root / 'config.yaml', source / 'linked.txt')
            except OSError:
                pass
        first, _, digest = build_plugin_package.build(source, self.root / 'a.zip')
        os.utime(source / 'plugin.py', (1, 1))
        second, _, again = build_plugin_package.build(source, self.root / 'b.zip')
        self.assertEqual(digest, again)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), digest)
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(archive.namelist(), ['lib/helper.py', 'manifest.json', 'plugin.py'])
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(build_plugin_package.main([str(source), '-o', str(self.root / 'c.zip'), '--json']), 0)
        self.assertEqual(json.loads(out.getvalue())['sha256'], digest)

    def test_builder_refuses_an_invalid_manifest(self):
        manifest = hook_manifest()
        manifest['permissions']['files'] = ['everything']
        source = write_plugin(self.root / 'src', manifest)
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(build_plugin_package.main([str(source), '-o', str(self.root / 'x.zip')]), 1)
        self.assertIn('not valid', err.getvalue())
        self.assertFalse((self.root / 'x.zip').exists())


class InstallFlow(Base):
    def test_install_lists_disabled_ungranted_with_record_and_prints_review(self):
        path, digest = self.build(sidebar_manifest())
        code, out, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 0, err)
        for text in ('demo-sidebar', '1.0.0', 'no, it runs only in the browser', path.name, digest, 'disabled'):
            self.assertIn(text, out)
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])
        item = self.item('demo-sidebar')
        self.assertEqual((item['origin'], item['status'], item['enabled'], item['consent']['status']),
                         ('installed', 'disabled', False, 'required'))
        self.assertEqual(item['install']['sha256'], digest)
        self.assertEqual(item['install']['source'], path.name)
        self.assertEqual(item['install']['version'], '1.0.0')
        self.assertEqual(item['compatibility'], {'host_api': 2, 'core': None})
        self.assertNotIn('demo-sidebar', self.config_path.read_text())
        self.assertFalse((host._root(self.config) / 'demo-sidebar' / 'consent.json').exists())
        if os.name != 'nt':
            target = self.installed_root() / 'demo-sidebar'
            self.assertEqual(stat.S_IMODE(self.installed_root().stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((target / 'web/app.js').stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((target / 'web').stat().st_mode), 0o700)
        # The CLI list reports the same.
        code, out, _ = self.cli('list')
        self.assertIn('demo-sidebar  1.0.0  installed  disabled', out)
        self.assertIn('demo-bundled  1.0.0  builtin  disabled', out)
        code, out, _ = self.cli('list', '--json')
        self.assertEqual(json.loads(out), host.list_plugins(self.config))

    def test_python_package_review_says_it_runs_code(self):
        path, digest = self.build(hook_manifest())
        code, out, err = self.cli('install', str(path), '--sha256', digest.upper(), '--version', '1.0.0', '--yes')
        self.assertEqual(code, 0, err)
        self.assertIn('yes, this package runs Python code on this machine', out)

    def test_wrapping_directory_is_stripped(self):
        source = write_plugin(self.root / 'src', sidebar_manifest())
        entries = [('wrap/', b'', stat.S_IFDIR | 0o755)] + [
            ('wrap/' + p.relative_to(source).as_posix(), p.read_bytes(), None)
            for p in sorted(source.rglob('*')) if p.is_file()]
        path = self.packages / 'wrapped.zip'
        path.write_bytes(raw_zip(entries))
        self.assertEqual(self.cli('install', str(path), '--yes')[0], 0)
        self.assertTrue((self.installed_root() / 'demo-sidebar/web/index.html').is_file())
        self.assertFalse((self.installed_root() / 'demo-sidebar/wrap').exists())

    def test_no_tty_without_yes_installs_nothing_and_interactive_yes_installs(self):
        path, _ = self.build(sidebar_manifest())
        code, _, err = self.cli('install', str(path), stdin=io.StringIO('yes\n'))
        self.assertEqual(code, 1)
        self.assertIn('Nothing was installed', err)
        self.assertEqual(self.landed(), [])

        class Terminal(io.StringIO):
            def isatty(self):
                return True
        self.assertEqual(self.cli('install', str(path), stdin=Terminal('no\n'))[0], 1)
        self.assertEqual(self.landed(), [])
        self.assertEqual(self.cli('install', str(path), stdin=Terminal('yes\n'))[0], 0)
        self.assertIn('demo-sidebar', self.landed())

    def test_hash_and_version_mismatch_stop_before_anything_is_written(self):
        path, digest = self.build(sidebar_manifest())
        for argv, phrase in ((('--sha256', '0' * 64), 'not the expected value'),
                             (('--sha256', 'xyz'), '64 hexadecimal'),
                             (('--version', '2.0.0'), 'not the requested 2.0.0')):
            code, out, err = self.cli('install', str(path), *argv, '--yes')
            self.assertEqual(code, 1)
            self.assertIn(phrase, err)
            self.assertNotIn('Traceback', err)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(self.landed(), [])

    def test_hash_is_checked_before_the_archive_is_opened(self):
        path = self.packages / 'garbage.zip'
        path.write_bytes(b'not a zip')
        with patch.object(install.zipfile, 'ZipFile', side_effect=AssertionError('opened')):
            code, _, err = self.cli('install', str(path), '--sha256', 'a' * 64, '--yes')
        self.assertEqual(code, 1)
        self.assertIn('not the expected value', err)

    def test_bundled_and_reserved_ids_are_refused(self):
        for manifest in (sidebar_manifest('demo-bundled'), sidebar_manifest('workspace'), hook_manifest('sponsorship')):
            path, _ = self.build(manifest)
            code, _, err = self.cli('install', str(path), '--yes')
            self.assertEqual(code, 1, manifest['id'])
            self.assertIn('bundled', err)
        self.assertEqual(self.landed(), [])
        self.assertEqual(self.cli('uninstall', 'demo-bundled', '--yes')[0], 1)
        self.assertTrue((self.bundled / 'demo-bundled/manifest.json').exists())

    def test_installed_directory_with_a_bundled_id_is_never_read(self):
        # Even when placed by hand, an installed copy cannot shadow bundled code.
        write_plugin(self.installed_root(), sidebar_manifest('demo-bundled', version='9.9.9'))
        write_plugin(self.installed_root(), sidebar_manifest('map'))
        items = [i for i in host.list_plugins(self.config) if i['id'] in ('demo-bundled', 'map')]
        self.assertEqual([(i['id'], i['origin'], i['version']) for i in items], [('demo-bundled', 'builtin', '1.0.0')])
        self.assertEqual(host.plugin_dir(self.config, 'demo-bundled'), self.bundled / 'demo-bundled')

    def test_symlinked_and_capped_installed_directories(self):
        write_plugin(self.root / 'elsewhere', sidebar_manifest('demo-linked'))
        self.installed_root().mkdir(parents=True)
        try:
            os.symlink(self.root / 'elsewhere/demo-linked', self.installed_root() / 'demo-linked',
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.assertIsNone(self.item('demo-linked'))
        self.assertIsNone(host.plugin_dir(self.config, 'demo-linked'))
        for index in range(40):
            write_plugin(self.installed_root(), sidebar_manifest(f'demo-{index:02d}'))
        items = host.list_plugins(self.config)
        self.assertEqual(len(items), host.MAX_PLUGINS)
        self.assertEqual(items[0]['id'], 'demo-bundled')   # bundled first, never pushed out
        path, _ = self.build(sidebar_manifest('demo-new'))
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('maximum', err)

    def test_incompatible_package_is_refused(self):
        path, _ = self.build(sidebar_manifest(core='>=99.0'))
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('>=99.0', err)
        self.assertIn(__version__, err)
        self.assertEqual(self.landed(), [])

    def test_run_lock_held_refuses_install_upgrade_and_uninstall(self):
        path, _ = self.build(sidebar_manifest())
        lock = run_state.acquire_lock(self.config)
        try:
            code, _, err = self.cli('install', str(path), '--yes')
            self.assertEqual(code, 1)
            self.assertIn('try again', err)
            self.assertEqual(self.landed(), [])
        finally:
            lock.close()
        self.assertEqual(self.cli('install', str(path), '--yes')[0], 0)
        upgraded, _ = self.build(sidebar_manifest(version='2.0.0'))
        lock = run_state.acquire_lock(self.config)
        try:
            self.assertEqual(self.cli('upgrade', str(upgraded), '--yes')[0], 1)
            self.assertEqual(self.cli('uninstall', 'demo-sidebar', '--yes')[0], 1)
        finally:
            lock.close()
        self.assertEqual(self.item('demo-sidebar')['version'], '1.0.0')

    def test_old_cli_forms_are_unchanged(self):
        path, _ = self.build(sidebar_manifest())
        self.assertEqual(self.cli('install', str(path), '--yes')[0], 0)
        code, out, _ = self.cli('review', 'demo-sidebar')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), self.item('demo-sidebar'))
        # Same refusal text and code as before for a non-interactive grant.
        code, _, err = self.cli('grant', 'demo-sidebar')
        self.assertEqual((code, err), (1, 'No grant recorded.\n'))
        fingerprint = self.item('demo-sidebar')['consent']['fingerprint']
        code, out, _ = self.cli('grant', 'demo-sidebar', '--fingerprint', fingerprint)
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith('Plugin settings saved.\n'))
        self.assertEqual(self.item('demo-sidebar')['status'], 'enabled')
        code, out, _ = self.cli('revoke', 'demo-sidebar', '--fingerprint', 'ignored-as-before')
        self.assertEqual(code, 0)
        self.assertEqual(self.item('demo-sidebar')['consent']['status'], 'required')
        code, _, err = self.cli('review', 'demo-missing')
        self.assertEqual((code, err), (1, 'Plugin unavailable or manifest invalid.\n'))
        # Options of the new actions are not accepted by the old ones, and vice versa.
        self.assertEqual(self.cli('review', 'demo-sidebar', '--yes')[0], 2)
        self.assertEqual(self.cli('list', 'demo-sidebar')[0], 2)
        self.assertEqual(self.cli('install')[0], 2)
        self.assertEqual(self.cli('list', '--fingerprint', 'x')[0], 2)


class UpgradeAndUninstall(Base):
    def install(self, manifest, **kwargs):
        path, digest = self.build(manifest, **kwargs)
        code, out, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 0, err)
        return digest

    def test_install_of_installed_refused_upgrade_replaces_and_keeps_grant_by_fingerprint(self):
        self.install(sidebar_manifest())
        self.write_config({'demo-sidebar': {'enabled': True}})
        host.grant(self.config, sidebar_manifest(), host.fingerprint(sidebar_manifest()), 'test')
        self.assertEqual(self.item('demo-sidebar')['status'], 'enabled')
        again, _ = self.build(sidebar_manifest(), name='again')
        code, _, err = self.cli('install', str(again), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('plugin upgrade', err)
        # Cosmetic upgrade (version and core range): grant and enabled state kept.
        path, digest = self.build(sidebar_manifest(version='1.1.0', core='>=1.0'))
        code, out, err = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 0, err)
        self.assertIn('1.0.0 -> 1.1.0', out)
        item = self.item('demo-sidebar')
        self.assertEqual((item['version'], item['status'], item['install']['sha256']), ('1.1.0', 'enabled', digest))
        self.assertIn('synthetic sidebar 1.1.0', (self.installed_root() / 'demo-sidebar/web/index.html').read_text())
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])
        # A changed permission declaration needs a new review; enabled stays as configured.
        changed = sidebar_manifest(version='1.2.0')
        del changed['permissions']['job_detail']
        path, _ = self.build(changed)
        code, out, _ = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 0)
        self.assertIn('permissions changed', out)
        item = self.item('demo-sidebar')
        self.assertEqual((item['enabled'], item['status'], item['consent']['status']),
                         (True, 'consent_required', 'changed'))
        # Downgrade is allowed and stated.
        path, _ = self.build(sidebar_manifest(version='0.9.0'))
        code, out, _ = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 0)
        self.assertIn('downgrade', out)

    def test_upgrade_of_a_not_installed_plugin_is_refused(self):
        path, _ = self.build(sidebar_manifest())
        code, _, err = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('plugin install', err)
        self.assertEqual(self.landed(), [])

    def test_failures_during_upgrade_restore_the_previous_package(self):
        first = self.install(sidebar_manifest())
        before = (self.installed_root() / 'demo-sidebar/web/index.html').read_bytes()
        path, _ = self.build(sidebar_manifest(version='2.0.0'))
        real_rename = install._rename
        calls = []

        def second_rename_fails(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError('simulated rename failure')
            return real_rename(source, target)
        for failure in (patch.object(install, 'write_record', side_effect=OSError('simulated full disk')),
                        patch.object(install, '_rename', side_effect=second_rename_fails),
                        patch.object(install, '_write_file', side_effect=OSError('simulated write failure'))):
            calls.clear()
            with failure:
                code, _, err = self.cli('upgrade', str(path), '--yes')
            self.assertEqual(code, 1)
            self.assertIn('previous state was kept', err)
            self.assertEqual((self.installed_root() / 'demo-sidebar/web/index.html').read_bytes(), before)
            item = self.item('demo-sidebar')
            self.assertEqual((item['version'], item['install']['sha256']), ('1.0.0', first))
            self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])

    def test_failed_first_install_leaves_nothing(self):
        path, _ = self.build(sidebar_manifest())
        with patch.object(install, 'write_record', side_effect=OSError('simulated')):
            self.assertEqual(self.cli('install', str(path), '--yes')[0], 1)
        self.assertEqual(self.landed(), [])

    def test_interrupted_upgrade_is_recovered_by_the_next_operation(self):
        self.install(sidebar_manifest())
        root = self.installed_root()
        # A crash between the two renames left the old package aside.
        os.rename(root / 'demo-sidebar', root / ('.old-demo-sidebar-' + 'a' * 32))
        (root / ('.staging-' + 'b' * 32)).mkdir()
        self.assertIsNone(self.item('demo-sidebar'))
        path, _ = self.build(sidebar_manifest(version='2.0.0'))
        code, _, err = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])
        self.assertEqual(self.item('demo-sidebar')['version'], '2.0.0')

    def test_uninstall_disables_revokes_and_keeps_data(self):
        self.install(hook_manifest())
        self.write_config({'demo-hook': {'enabled': True}, 'other': {'enabled': False}})
        host.grant(self.config, hook_manifest(), host.fingerprint(hook_manifest()), 'test')
        results = host._root(self.config) / 'demo-hook' / 'results.json'
        results.write_text('{"kept": true}')
        code, _, err = self.cli('uninstall', 'demo-hook', stdin=io.StringIO('yes\n'))
        self.assertEqual(code, 1)          # no TTY, no --yes
        self.assertIn('demo-hook', self.landed())
        code, out, err = self.cli('uninstall', 'demo-hook', '--yes')
        self.assertEqual(code, 0, err)
        self.assertIn('kept', out)
        self.assertEqual(self.landed(), [])
        self.assertFalse((host._root(self.config) / 'demo-hook' / 'consent.json').exists())
        self.assertEqual(results.read_text(), '{"kept": true}')
        text = self.config_path.read_text()
        self.assertIn('demo-hook:\n    enabled: false', text)
        self.assertIn('other:\n    enabled: false', text)
        self.assertIsNone(self.item('demo-hook'))
        code, _, err = self.cli('uninstall', 'demo-hook', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('not an installed plugin', err)

    def test_uninstall_also_drops_a_leftover_backup(self):
        self.install(sidebar_manifest())
        root = self.installed_root()
        write_plugin(self.root / 'aside', sidebar_manifest())
        os.rename(self.root / 'aside/demo-sidebar', root / ('.old-demo-sidebar-' + 'c' * 32))
        with patch.object(install, 'recover'):      # as if its deletion had failed
            self.assertEqual(self.cli('uninstall', 'demo-sidebar', '--yes')[0], 0)
        install.recover(self.config)
        self.assertEqual(self.landed(), [])
        self.assertIsNone(self.item('demo-sidebar'))

    def test_uninstall_tolerates_a_missing_or_broken_manifest(self):
        self.install(sidebar_manifest())
        self.write_config({'demo-sidebar': {'enabled': True}})
        host.grant(self.config, sidebar_manifest(), host.fingerprint(sidebar_manifest()), 'test')
        (self.installed_root() / 'demo-sidebar/manifest.json').unlink()
        self.assertEqual(self.item('demo-sidebar')['status'], 'error')
        code, _, err = self.cli('uninstall', 'demo-sidebar', '--yes')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.landed(), [])
        self.assertFalse((host._root(self.config) / 'demo-sidebar' / 'consent.json').exists())
        self.assertIn('enabled: false', self.config_path.read_text())

    def test_uninstall_of_a_never_configured_plugin_leaves_config_untouched(self):
        self.install(sidebar_manifest())
        before = self.config_path.read_bytes()
        self.assertEqual(self.cli('uninstall', 'demo-sidebar', '--yes')[0], 0)
        self.assertEqual(self.config_path.read_bytes(), before)

    # ---- review round: refusals and transaction boundaries --------------------------

    @unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'needs POSIX symlinks')
    def test_symlinked_or_non_directory_package_root_is_refused_before_anything_changes(self):
        self.install(sidebar_manifest())
        root = self.installed_root()
        elsewhere = self.root / 'elsewhere'
        root.rename(elsewhere)
        os.symlink(elsewhere, root)
        self.write_config({'demo-sidebar': {'enabled': True}})
        before = self.config_path.read_bytes()
        code, _, err = self.cli('uninstall', 'demo-sidebar', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('symbolic link', err)
        self.assertTrue((elsewhere / 'demo-sidebar' / 'manifest.json').is_file())     # nothing deleted through the link
        self.assertEqual(self.config_path.read_bytes(), before)                        # nor disabled
        path, _ = self.build(sidebar_manifest('demo-other'))
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('symbolic link', err)
        self.assertFalse((elsewhere / 'demo-other').exists())
        root.unlink()
        root.write_text('not a directory')
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('not a directory', err)
        self.assertNotIn('Traceback', err)

    def test_first_install_is_off_and_ungranted_even_with_an_earlier_setting_and_grant(self):
        manifest = hook_manifest()
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, manifest, host.fingerprint(manifest), 'test')       # left by an earlier install
        path, _ = self.build(manifest)
        code, out, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 0, err)
        item = self.item('demo-hook')
        self.assertEqual((item['enabled'], item['status'], item['consent']['status']), (False, 'disabled', 'required'))
        self.assertFalse((host._root(self.config) / 'demo-hook' / 'consent.json').exists())
        self.assertIn('It is disabled', out)

    def test_directory_sync_failure_rolls_back_package_and_record(self):
        first = self.install(sidebar_manifest())
        path, _ = self.build(sidebar_manifest(version='2.0.0'))
        with patch.object(host, '_sync_directory', side_effect=OSError('simulated sync failure')):
            code, _, err = self.cli('upgrade', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('previous state was kept', err)
        item = self.item('demo-sidebar')
        self.assertEqual((item['version'], item['install']['version'], item['install']['sha256']), ('1.0.0', '1.0.0', first))
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])
        other, _ = self.build(sidebar_manifest('demo-new'))
        with patch.object(host, '_sync_directory', side_effect=OSError('simulated sync failure')):
            self.assertEqual(self.cli('install', str(other), '--yes')[0], 1)
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])

    def test_configuration_pointing_elsewhere_after_the_prompt_changes_nothing(self):
        path, _ = self.build(sidebar_manifest())
        other_db = self.root / 'other' / 'jobs.db'
        other_db.parent.mkdir()

        def repoint(*_args, **_kwargs):
            self.config_path.write_text(f'db_path: {json.dumps(str(other_db))}\n', encoding='utf-8')
            return True
        with patch.object(cli, '_confirm', side_effect=repoint):
            code, _, err = self.cli('install', str(path))
        self.assertEqual(code, 1)
        self.assertIn('another database', err)
        self.assertEqual(self.landed(), [])
        self.assertFalse((other_db.parent / '.jobs.db.plugin-packages').exists())

    def test_recovery_never_shows_old_provenance_for_new_code(self):
        self.install(sidebar_manifest())
        root, token = self.installed_root(), 'a' * 32
        # State left by a crash after the new package was placed, before its record was written.
        (root / 'demo-sidebar.install.json').rename(root / f'.old-demo-sidebar-{token}.json')
        (root / 'demo-sidebar').rename(root / f'.old-demo-sidebar-{token}')
        write_plugin(self.root / 'fresh', sidebar_manifest(version='2.0.0')).rename(root / 'demo-sidebar')
        install.recover(self.config)
        item = self.item('demo-sidebar')
        self.assertEqual((item['version'], item['install']), ('2.0.0', None))
        self.assertEqual(self.landed(), ['demo-sidebar'])
        # State left by a crash before the new package was placed: everything comes back.
        token = 'b' * 32
        install.write_record(self.config, install.Package(manifest=sidebar_manifest(version='2.0.0'), prefix=''),
                             install.Archive(data=b'', sha256='c' * 64, source='x.zip'))
        (root / 'demo-sidebar.install.json').rename(root / f'.old-demo-sidebar-{token}.json')
        (root / 'demo-sidebar').rename(root / f'.old-demo-sidebar-{token}')
        install.recover(self.config)
        item = self.item('demo-sidebar')
        self.assertEqual((item['version'], item['install']['sha256']), ('2.0.0', 'c' * 64))
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])

    def test_upgrade_says_before_confirming_whether_new_code_runs_without_review(self):
        self.install(sidebar_manifest())
        same, _ = self.build(sidebar_manifest(version='2.0.0'))
        self.write_config({'demo-sidebar': {'enabled': True}})
        host.grant(self.config, sidebar_manifest(), host.fingerprint(sidebar_manifest()), 'test')
        code, out, _ = self.cli('upgrade', str(same))            # no terminal, no --yes: nothing happens
        self.assertEqual(code, 1)
        self.assertIn('existing grant stays valid', out)
        self.assertIn('runs without another review', out)
        self.assertEqual(self.item('demo-sidebar')['version'], '1.0.0')
        changed = sidebar_manifest(version='3.0.0')
        del changed['permissions']['job_detail']
        other, _ = self.build(changed)
        code, out, _ = self.cli('upgrade', str(other))
        self.assertEqual(code, 1)
        self.assertIn('stays inactive until you review and grant', out)
        fresh, _ = self.build(sidebar_manifest('demo-fresh'))
        self.assertIn('turned off and without a permission grant', self.cli('install', str(fresh))[1])

    def test_review_text_cannot_hide_or_forge_lines(self):
        for field, value in (('name', {'en': 'Nice\x1b[8m', 'zh': '好'}), ('version', '1.0\nPython:   no'),
                             ('description', {'en': 'ok\u202egnp.exe', 'zh': '好'})):
            manifest = sidebar_manifest('demo-esc')
            manifest[field] = value
            path, _ = self.build_unchecked(manifest)
            code, out, err = self.cli('install', str(path), '--yes')
            self.assertEqual(code, 1, field)
            self.assertIn('control or direction characters', err)
            self.assertNotIn('\x1b', out + err)
            self.assertEqual(self.landed(), [])
        # Text already on disk (an older record or manifest) is escaped when listed.
        self.assertEqual(install.visible('a\x1b[8mb\nc\u202e'), 'a\\x1b[8mb\\x0ac\\u202e')
        self.install(sidebar_manifest())
        record = self.installed_root() / 'demo-sidebar.install.json'
        (self.installed_root() / 'demo-sidebar' / 'manifest.json').write_text(
            json.dumps({**sidebar_manifest(), 'version': '1\x1b[8m'}), encoding='utf-8')
        code, out, _ = self.cli('list')
        self.assertEqual(code, 0)
        self.assertNotIn('\x1b', out)
        self.assertTrue(record.exists())

    def test_interrupted_upgrade_is_repaired_by_list_and_by_the_next_run(self):
        for repair in ('list', 'run'):
            with self.subTest(repair=repair):
                if not (self.installed_root() / 'demo-sidebar').exists():
                    self.install(sidebar_manifest())
                root = self.installed_root()
                (root / 'demo-sidebar').rename(root / ('.old-demo-sidebar-' + 'd' * 32))   # killed mid-upgrade
                self.assertIsNone(self.item('demo-sidebar'))
                if repair == 'list':
                    code, out, _ = self.cli('list')
                    self.assertIn('demo-sidebar', out)
                else:
                    self.write_config({'demo-sidebar': {'enabled': True}})
                    host.dispatch(ConfigEditor(self.config, self.config_path).plugin_config(), 'after_fetch')
                self.assertEqual(self.item('demo-sidebar')['version'], '1.0.0')
                self.assertEqual([n for n in self.landed() if n.startswith('.')], [])

    def test_stale_installed_copy_of_a_now_bundled_id_can_be_removed(self):
        self.install(sidebar_manifest())
        self.write_config({'demo-sidebar': {'enabled': True}})
        host.grant(self.config, sidebar_manifest(), host.fingerprint(sidebar_manifest()), 'test')
        write_plugin(self.bundled, sidebar_manifest())                 # a later release bundles the id
        self.assertEqual(self.item('demo-sidebar')['origin'], 'builtin')
        before = self.config_path.read_bytes()
        code, out, err = self.cli('uninstall', 'demo-sidebar', '--yes')
        self.assertEqual(code, 0, err)
        self.assertIn('installed copy', out)
        self.assertEqual(self.landed(), [])
        self.assertEqual(self.config_path.read_bytes(), before)        # the bundled plugin keeps its setting
        self.assertTrue((host._root(self.config) / 'demo-sidebar' / 'consent.json').exists())   # and its grant
        code, _, err = self.cli('uninstall', 'demo-sidebar', '--yes')
        self.assertEqual(code, 1)
        self.assertIn('bundled', err)

    # ---- re-check round ---------------------------------------------------------------

    def test_recovery_inside_a_run_keeps_timeouts_and_survives_cleanup_errors(self):
        from jobs import RunTimeout
        self.install(hook_manifest())
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, hook_manifest(), host.fingerprint(hook_manifest()), 'test')
        config = ConfigEditor(self.config, self.config_path).plugin_config()
        with patch.object(install, 'recover', side_effect=RunTimeout()):
            with self.assertRaises(RunTimeout):
                host.dispatch(config, 'after_fetch')
        calls = []
        with patch.object(install, 'recover', side_effect=install.InstallError('cleanup failed')), \
                patch.object(host, '_run_worker', side_effect=lambda *a, **k: calls.append(a[1]) or {'errors': []}):
            host.dispatch(config, 'after_fetch')         # a failed cleanup does not stop usable plugins
        self.assertEqual(host.read_debug(config, 'demo-hook')['entries'], [])

    def test_analyze_recovers_before_it_decides_whether_to_prepare_jobs(self):
        self.install(hook_manifest())
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, hook_manifest(), host.fingerprint(hook_manifest()), 'test')
        root = self.installed_root()
        (root / 'demo-hook').rename(root / ('.old-demo-hook-' + 'e' * 32))      # killed mid-upgrade
        config = ConfigEditor(self.config, self.config_path).plugin_config()
        seen = []

        def record(config, hook, jobs=None, analysis_id=None, recover=True):
            seen.append((hook, jobs, analysis_id))

        class Conn:
            def execute(self, sql):
                class Row:
                    def fetchone(self_inner):
                        return (7,)
                return Row()
        with patch.object(host, 'dispatch', side_effect=record), patch.object(host, '_analysis_jobs', return_value=[{'uid': 'synthetic:one', 'company': 'Co'}]):
            host.after_analyze(config, Conn())
        self.assertEqual(seen, [('after_analyze_success', [{'uid': 'synthetic:one', 'company': 'Co'}], 7)])

    def test_analyze_with_a_failed_first_repair_never_runs_a_plugin_without_jobs(self):
        self.install(hook_manifest())
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, hook_manifest(), host.fingerprint(hook_manifest()), 'test')
        root = self.installed_root()
        (root / 'demo-hook').rename(root / ('.old-demo-hook-' + 'e' * 32))
        config = ConfigEditor(self.config, self.config_path).plugin_config()
        real, attempts, ran = install.recover, [], []

        def fail_once(cfg):
            attempts.append(1)
            if len(attempts) == 1:
                raise install.InstallError('transient')
            return real(cfg)

        class Conn:
            def execute(self, sql):
                raise AssertionError('no jobs are prepared when no plugin is runnable')
        with patch.object(install, 'recover', side_effect=fail_once), \
                patch.object(host, '_run_worker', side_effect=lambda *a, **k: ran.append(a)):
            host.after_analyze(config, Conn())
        self.assertEqual((len(attempts), ran), (1, []))      # one decision, no call without jobs

    def test_interrupted_cleanup_never_pairs_an_old_record_with_new_files(self):
        self.install(sidebar_manifest())
        root, token = self.installed_root(), '1' * 32
        # New package placed, record not written yet; then cleanup stops half way, twice.
        (root / 'demo-sidebar.install.json').rename(root / f'.old-demo-sidebar-{token}.json')
        (root / 'demo-sidebar').rename(root / f'.old-demo-sidebar-{token}')
        write_plugin(self.root / 'fresh2', sidebar_manifest(version='2.0.0')).rename(root / 'demo-sidebar')
        with patch.object(install, '_remove_tree', side_effect=OSError('interrupted')):
            with self.assertRaises(install.InstallError):
                install.recover(self.config)
        self.assertFalse((root / f'.old-demo-sidebar-{token}.json').exists())     # the old record went first
        for _ in range(2):
            install.recover(self.config)
            item = self.item('demo-sidebar')
            self.assertEqual((item['version'], item['install']), ('2.0.0', None))
        self.assertEqual(self.landed(), ['demo-sidebar'])

    def test_interrupt_between_placing_and_flagging_does_not_restore_the_old_record(self):
        first = self.install(sidebar_manifest())
        path, _ = self.build(sidebar_manifest(version='2.0.0'))
        real_rename, seen = install._rename, []

        def interrupt_after_placing(source, target):
            real_rename(source, target)
            seen.append(Path(source).name)
            if Path(source).name.startswith('.staging-'):
                raise KeyboardInterrupt()
        with patch.object(install, '_rename', side_effect=interrupt_after_placing):
            with self.assertRaises(KeyboardInterrupt):
                self.cli('upgrade', str(path), '--yes')
        item = self.item('demo-sidebar')          # rolled back as a whole: old files with their own record
        self.assertEqual((item['version'], item['install']['sha256']), ('1.0.0', first))

    def test_record_backup_without_a_package_backup_is_given_back(self):
        first = self.install(sidebar_manifest())
        root = self.installed_root()
        # Killed after the record moved aside, before the package did.
        (root / 'demo-sidebar.install.json').rename(root / ('.old-demo-sidebar-' + 'f' * 32 + '.json'))
        install.recover(self.config)
        self.assertEqual(self.item('demo-sidebar')['install']['sha256'], first)
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])
        # A leftover beside a package that already has its record is only removed.
        (root / ('.old-demo-sidebar-' + 'f' * 32 + '.json')).write_text('{"stale": true}')
        install.recover(self.config)
        self.assertEqual(self.item('demo-sidebar')['install']['sha256'], first)
        self.assertEqual(self.landed(), ['demo-sidebar', 'demo-sidebar.install.json'])

    def test_list_fails_when_an_interrupted_upgrade_cannot_be_repaired(self):
        self.install(sidebar_manifest())
        with patch.object(install, 'recover', side_effect=install.InstallError('An interrupted plugin change could not be cleaned up (PermissionError).')):
            code, out, err = self.cli('list')
        self.assertEqual(code, 1)
        self.assertIn('may be missing a plugin', err)
        self.assertIn('demo-bundled', out)

    def test_failed_first_install_says_the_earlier_setting_and_grant_were_cleared(self):
        manifest = hook_manifest()
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, manifest, host.fingerprint(manifest), 'test')
        path, _ = self.build(manifest)
        with patch.object(install, '_write_file', side_effect=OSError('simulated')):
            code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1)
        self.assertIn('was not installed', err)
        self.assertIn('was cleared', err)
        self.assertNotIn('previous state was kept', err)
        self.assertEqual(self.landed(), [])

    def test_direction_marks_are_escaped_in_every_command_output(self):
        for mark in ('\u061c', '\u202e', '\u2067'):
            self.assertNotIn(mark, install.visible('a' + mark + 'b'))
            manifest = sidebar_manifest('demo-mark')
            manifest['name'] = {'en': 'Tidy' + mark, 'zh': '好'}
            path, _ = self.build_unchecked(manifest)
            self.assertEqual(self.cli('install', str(path), '--yes')[0], 1, repr(mark))
        self.install(sidebar_manifest())
        target = self.installed_root() / 'demo-sidebar' / 'manifest.json'
        target.write_text(json.dumps({**sidebar_manifest(), 'name': {'en': 'Shown\u202e', 'zh': '好\u061c'}}), encoding='utf-8')
        for argv in (('list', '--json'), ('review', 'demo-sidebar')):
            code, out, _ = self.cli(*argv)
            self.assertEqual(code, 0, argv)
            self.assertNotIn('\u202e', out)
            self.assertNotIn('\u061c', out)
            self.assertIn('\\u202e', out)                       # still the same value once decoded
            data = json.loads(out)
            item = data if isinstance(data, dict) else next(i for i in data if i['id'] == 'demo-sidebar')
            self.assertEqual(item['name']['en'], 'Shown\u202e')

    def test_missing_or_damaged_record_only_hides_provenance(self):
        self.install(sidebar_manifest())
        record = self.installed_root() / 'demo-sidebar.install.json'
        record.write_text('{broken')
        item = self.item('demo-sidebar')
        self.assertEqual((item['install'], item['status']), (None, 'disabled'))
        record.unlink()
        self.assertIsNone(self.item('demo-sidebar')['install'])


class ArchiveRejections(Base):
    def good_entries(self, manifest=None):
        manifest = manifest or sidebar_manifest()
        return [('manifest.json', json.dumps(manifest).encode(), None),
                ('web/index.html', b'<p>x</p>', None), ('web/compare.html', b'<p>y</p>', None)]

    def refuse(self, data, phrase):
        path = self.packages / 'hostile.zip'
        path.write_bytes(data)
        code, out, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 1, (phrase, out))
        self.assertIn(phrase, err)
        self.assertNotIn('Traceback', err)
        self.assertEqual(self.landed(), [], phrase)
        self.assertFalse((self.root / 'evil').exists())

    def test_every_listed_rejection(self):
        good = self.good_entries()
        cases = [
            (good + [('../evil', b'x', None)], '".."'),
            (good + [('web/../../evil', b'x', None)], '".."'),
            (good + [('/abs/evil', b'x', None)], 'absolute path'),
            (good + [('C:/evil', b'x', None)], 'absolute path'),
            (good + [('web//evil', b'x', None)], 'empty'),
            (good + [('web/./evil', b'x', None)], '"."'),
            (good + [('web/link', b'/etc/passwd', stat.S_IFLNK | 0o777)], 'symbolic link'),
            (good + [('web/device', b'', stat.S_IFCHR | 0o600)], 'not a regular file'),
            (good + [('web/fifo', b'', stat.S_IFIFO | 0o600)], 'not a regular file'),
            (good + [('web/App.js', b'1', None), ('web/app.js', b'2', None)], 'differ only by case'),
            (good + [('web/index.html', b'again', None)], 'duplicate'),
            (good + [('web/a.js', b'1', None), ('web/a.js/b.js', b'2', None)], 'file name as a directory'),
            (good + [('web/has space.js', b'1', None)], 'outside letters'),
            (good + [('web/.hidden', b'1', None)], 'outside letters'),
            (good + [('web/nul.js', b'1', None)], 'outside letters'),
            (good + [('web/a:b', b'1', None)], 'outside letters'),
            (good[1:], 'no manifest.json'),
            ([('a/manifest.json', good[0][1], None), ('b/x', b'1', None)], 'no manifest.json'),
            ([('manifest.json', b'{not json', None)], 'not valid JSON'),
        ]
        for entries, phrase in cases:
            with self.subTest(phrase=phrase, names=[e[0] for e in entries[3:]]):
                self.refuse(raw_zip(entries), phrase)

    def test_backslash_names_are_refused(self):
        data = raw_zip(self.good_entries() + [('webzzQzzevil.js', b'1', None)])
        self.refuse(data.replace(b'zzQzz', b'zz\\zz'), 'unsafe name')

    def test_encrypted_entries_are_refused(self):
        data = raw_zip(self.good_entries())
        self.refuse(patch_central(data, 'web/index.html', flags=0x1), 'encrypted')

    def test_manifest_rules(self):
        wrong = sidebar_manifest()
        wrong['id'] = 'Demo_Sidebar'
        self.refuse(raw_zip([('manifest.json', json.dumps(wrong).encode(), None)] + self.good_entries()[1:]), 'manifest id')
        unknown = sidebar_manifest()
        unknown['permissions']['network'] = [{'purpose': 'anything', 'targets': ['example.invalid']}]
        self.refuse(raw_zip(self.good_entries(unknown)), 'not valid for this host')
        hook = hook_manifest()
        hook['permissions']['execution'] = ['python_worker', 'shell']
        self.refuse(raw_zip([('manifest.json', json.dumps(hook).encode(), None), ('plugin.py', b'', None)]),
                    'not valid for this host')
        bad_range = sidebar_manifest(core='>1.0')
        self.refuse(raw_zip(self.good_entries(bad_range)), 'not valid for this host')
        # The manifest names an entry that is not in the package.
        self.refuse(raw_zip(self.good_entries()[:2]), 'web/compare.html')
        self.refuse(raw_zip([('manifest.json', json.dumps(hook_manifest()).encode(), None)]), 'plugin.py')

    def test_archive_written_with_writestr_installs(self):
        manifest = sidebar_manifest('demo-plain')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:      # modes 0600, no file-type bits
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr('web/index.html', '<p>plain</p>')
            archive.writestr('web/compare.html', '<p>plain</p>')
        path = self.packages / 'plain.zip'
        path.write_bytes(buffer.getvalue())
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 0, err)
        self.assertTrue((self.installed_root() / 'demo-plain' / 'web' / 'index.html').is_file())

    def test_entry_count_uses_the_same_directory_as_the_zip_reader(self):
        # A zip64 archive whose locator points at a decoy "empty" end record placed earlier:
        # the reader uses the record right before the locator, and so must the count.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for n in range(6):
                with archive.open(zipfile.ZipInfo(f'f{n}.txt'), 'w', force_zip64=True) as handle:
                    handle.write(b'x')
        plain = buffer.getvalue()
        end = plain.rfind(b'PK\x05\x06')
        size, offset = struct.unpack_from('<LL', plain, end + 12)
        directory = plain[offset:offset + size]
        decoy = b'PK\x06\x06' + struct.pack('<QHHLLQQQQ', 44, 45, 45, 0, 0, 0, 0, 0, 0)
        real = b'PK\x06\x06' + struct.pack('<QHHLLQQQQ', 44, 45, 45, 0, 0, 6, 6, size, len(decoy) + offset)
        locator = b'PK\x06\x07' + struct.pack('<LQL', 0, 0, 1)          # points at the decoy (offset 0)
        tail = struct.pack('<4sHHHHLLH', b'PK\x05\x06', 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
        crafted = decoy + plain[:offset] + directory + real + locator + tail
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(crafted)).infolist()), 6)      # what the reader sees
        with patch.object(install, 'MAX_ENTRIES', 4), \
                patch.object(zipfile, 'ZipFile', side_effect=AssertionError('central directory was parsed')):
            with self.assertRaises(install.InstallError) as error:
                install._open_archive(crafted)
        self.assertIn('more than 4 entries', error.exception.message)
        # Ordinary archives are still accepted, zip64 and with a comment included.
        for comment in (b'', b'a plain archive comment'):
            ordinary = io.BytesIO()
            with zipfile.ZipFile(ordinary, 'w') as archive:
                archive.comment = comment
                with archive.open(zipfile.ZipInfo('a.txt'), 'w', force_zip64=True) as handle:
                    handle.write(b'x')
            install._check_directory_size(ordinary.getvalue())

    def test_size_entry_and_bomb_limits(self):
        good = self.good_entries()
        with patch.object(install, 'MAX_ENTRIES', 4):
            self.refuse(raw_zip(good + [('web/a.js', b'1', None), ('web/b.js', b'2', None)]), 'more than 4 entries')
            # The limit is applied before zipfile builds an object per record.
            crowded = raw_zip(good + [('web/a.js', b'1', None), ('web/b.js', b'2', None)])
            with patch.object(zipfile, 'ZipFile', side_effect=AssertionError('central directory was parsed')):
                self.refuse(crowded, 'more than 4 entries')
        with patch.object(install, 'MAX_FILE', 1024 * 1024):
            self.refuse(raw_zip(good + [('web/big.js', b'\0' * (1024 * 1024 + 1), None)]), '64 MiB')
        with patch.object(install, 'MAX_TOTAL', 2 * 1024 * 1024):
            # Highly compressible: a few KiB on disk, over the unpacked limit.
            entries = good + [(f'web/z{i}.js', b'\0' * (900 * 1024), None) for i in range(3)]
            data = raw_zip(entries)
            self.assertLess(len(data), 64 * 1024)
            self.refuse(data, 'more than 256 MiB')
        with patch.object(install, 'MAX_ARCHIVE', 1024):
            self.refuse(raw_zip(good + [('web/r.js', os.urandom(2048), None)]), '128 MiB')
        # A lying size: declared 100 bytes, 4 MiB of data inside.
        data = raw_zip(good + [('web/bomb.js', b'\0' * (4 * 1024 * 1024), None)])
        self.refuse(patch_central(data, 'web/bomb.js', size=100), 'damaged')
        self.refuse(b'PK\x03\x04 not really a zip', 'not a valid .zip')


class Download(Base):
    URL = 'https://plugins.example.invalid/demo-sidebar-1.0.0.zip'

    def package(self):
        path, digest = self.build(sidebar_manifest())
        return path.read_bytes(), digest

    def test_https_download_with_hash_and_safe_headers(self):
        data, digest = self.package()
        network = FakeHTTPS({self.URL + '?token=secret': (200, {'Content-Length': str(len(data))}, data)})
        archive = install.obtain(self.URL + '?token=secret#frag', digest, handlers=[network])
        self.assertEqual(archive.sha256, digest)
        self.assertEqual(archive.source, self.URL)          # no query or fragment recorded
        (url, headers), = network.seen
        self.assertEqual(headers.get('User-agent'), 'au-job-board/' + __version__)
        self.assertTrue(set(headers) <= {'User-agent', 'Host', 'Connection'}, headers)
        package = install.inspect(archive.data)
        lock = run_state.acquire_lock(self.config)
        try:
            install.install(self.config, archive, package)
        finally:
            lock.close()
        self.assertEqual(self.item('demo-sidebar')['install']['source'], self.URL)

    def test_url_rules(self):
        data, digest = self.package()
        for url, phrase in (('http://plugins.example.invalid/x.zip', 'https://'),
                            ('ftp://plugins.example.invalid/x.zip', 'https://'),
                            ('https://user:pass@plugins.example.invalid/x.zip', 'user name or password'),
                            ('https://token@plugins.example.invalid/x.zip', 'user name or password')):
            with self.assertRaises(install.InstallError) as error:
                install.obtain(url, digest, handlers=[FakeHTTPS({})])
            self.assertIn(phrase, error.exception.message)
        with self.assertRaises(install.InstallError) as error:
            install.obtain(self.URL, None, handlers=[FakeHTTPS({self.URL: (200, {}, data)})])
        self.assertIn('--sha256', error.exception.message)
        with self.assertRaises(install.InstallError) as error:
            install.obtain(self.URL, '0' * 64, handlers=[FakeHTTPS({self.URL: (200, {}, data)})])
        self.assertIn('not the expected value', error.exception.message)

    def test_redirects_only_to_https_and_at_most_five(self):
        data, digest = self.package()
        hops = [f'https://mirror{i}.example.invalid/p.zip' for i in range(7)]
        routes = {self.URL: (302, {'Location': hops[0]}, b'')}
        for i in range(6):
            routes[hops[i]] = (302, {'Location': hops[i + 1]}, b'')
        routes[hops[6]] = (200, {}, data)
        with self.assertRaises(install.InstallError) as error:
            install.obtain(self.URL, digest, handlers=[FakeHTTPS(routes)])
        self.assertIn('more than 5', error.exception.message)
        routes[hops[4]] = (200, {}, data)       # five redirects: accepted
        self.assertEqual(install.obtain(self.URL, digest, handlers=[FakeHTTPS(routes)]).sha256, digest)
        for target in ('http://mirror.example.invalid/p.zip', 'https://u:p@mirror.example.invalid/p.zip'):
            network = FakeHTTPS({self.URL: (302, {'Location': target}, b'')})
            with self.assertRaises(install.InstallError) as error:
                install.obtain(self.URL, digest, handlers=[network])
            self.assertIn('redirected', error.exception.message)
            self.assertEqual(len(network.seen), 1)

    def test_size_time_and_failed_downloads_install_nothing(self):
        data, digest = self.package()
        with patch.object(install, 'MAX_ARCHIVE', len(data) - 1):
            for headers in ({'Content-Length': str(len(data))}, {}):
                with self.assertRaises(install.InstallError) as error:
                    install.obtain(self.URL, digest, handlers=[FakeHTTPS({self.URL: (200, headers, data)})])
                self.assertIn('128 MiB', error.exception.message)
        ticks = iter(range(0, 1000, 61))
        with self.assertRaises(install.InstallError) as error:
            install.download(self.URL, handlers=[FakeHTTPS({self.URL: (200, {}, data)})], clock=lambda: next(ticks))
        self.assertIn('60 seconds', error.exception.message)
        # The budget also covers the last read: time that runs out at end of data is refused.
        late = iter([0, 0, 120, 120, 120])
        with self.assertRaises(install.InstallError) as error:
            install.download(self.URL, handlers=[FakeHTTPS({self.URL: (200, {}, b'')})], clock=lambda: next(late))
        self.assertIn('60 seconds', error.exception.message)
        # A Location with a space or a raw non-ASCII character is percent-encoded, as urllib does.
        spaced = FakeHTTPS({self.URL: (302, {'Location': '/package name caf\u00e9.zip'}, b''),
                            'https://plugins.example.invalid/package%20name%20caf%E9.zip': (200, {}, data)})
        self.assertEqual(install.download(self.URL, handlers=[spaced]), data)
        # Redirects share the budget: a slow chain is refused, and each hop only gets what is left.
        hop = 'https://example.org/hop'
        chain = FakeHTTPS({self.URL: (302, {'Location': hop + '1'}, b'x' * 10),
                           hop + '1': (302, {'Location': hop + '2'}, b''), hop + '2': (200, {}, data)})
        slow = iter(range(0, 10000, 25))
        with self.assertRaises(install.InstallError) as error:
            install.download(self.URL, handlers=[chain], clock=lambda: next(slow))
        self.assertIn('60 seconds', error.exception.message)
        self.assertLess(len(chain.seen), 3)                      # the last hop was never requested
        with self.assertRaises(install.InstallError) as error:
            install.obtain(self.URL, digest, handlers=[FakeHTTPS({self.URL: (404, {}, b'')})])
        self.assertIn('404', error.exception.message)

        class Broken(FakeHTTPS):
            def https_open(self, request):
                raise http.client.IncompleteRead(b'partial')
        with self.assertRaises(install.InstallError):
            install.obtain(self.URL, digest, handlers=[Broken({})])
        self.assertEqual(self.landed(), [])

    def test_cli_url_install_needs_a_hash_and_uses_the_download(self):
        data, digest = self.package()
        code, _, err = self.cli('install', self.URL, '--yes')
        self.assertEqual(code, 1)
        self.assertIn('--sha256', err)
        with patch.object(install, 'download', return_value=data) as fetched:
            code, out, err = self.cli('install', self.URL, '--sha256', digest, '--yes')
        self.assertEqual(code, 0, err)
        fetched.assert_called_once()
        self.assertIn(self.URL, out)


class Compatibility(Base):
    def test_range_grammar(self):
        for text in ('>=1.4.0', '<2', '>=1.4.0,<2.0.0', '<2,>=1', '>=0', '>=1.2.3.4', '<10.0.0.0', '>=2,<1'):
            host.parse_core_range(text)
        for text in ('', '>1', '<=2', '==1', '=1', '1.4', '>=1.4.0,<2,<3', '>=1,>=2', ' >=1', '>=1 ,<2',
                     '>=1, <2', '>=01', '>=1.02', '>=1.2.3.4.5', '>=1.', '>=.1', '>=a', '>=1;<2', '>=1,<2,',
                     '>=1,,<2', '>=-1', '>=1.4.0-beta', '>=1e3', '>=１', None, 1, ['>=1'], '>=' + '1' * 70):
            with self.assertRaises(ValueError, msg=repr(text)):
                host.parse_core_range(text)

    def test_boundaries_including_four_part_versions(self):
        cases = [('>=1.4.0', '1.4.0', True), ('>=1.4', '1.4.0', True), ('>=1.4.0.1', '1.4.0', False),
                 ('<1.4', '1.4.0', False), ('<1.4.0.1', '1.4.0', True), ('<1.4.1', '1.4.0', True),
                 ('>=1.4.0.1', '1.4.0.1', True), ('<1.4.0.1', '1.4.0.1', False), ('<1.4.0.2', '1.4.0.1', True),
                 ('>=1.10', '1.9.0', False), ('>=1.9', '1.10.0', True), ('>=1.4.0,<2.0.0', '2.0.0', False),
                 ('>=1.4.0,<2.0.0', '1.99.99.99', True), ('<2,>=1', '0.9', False), ('>=2,<1', '1.5', False)]
        for text, version, expected in cases:
            self.assertIs(host.core_satisfies(text, version), expected, (text, version))
        self.assertIsNone(host.incompatibility(sidebar_manifest(core='>=1.0'), '1.4.0'))
        self.assertEqual(host.incompatibility(sidebar_manifest(core='<1.4'), '1.4.0'),
                         {'core': '1.4.0', 'requires': '<1.4'})

    def test_core_is_not_part_of_the_fingerprint(self):
        for manifest in (sidebar_manifest(), hook_manifest(),
                         json.loads((ROOT / 'plugins/map/manifest.json').read_text())):
            ranged = copy.deepcopy(manifest)
            ranged['compatibility'] = {**ranged['compatibility'], 'core': '>=1.0,<9'}
            self.assertEqual(host.fingerprint(ranged), host.fingerprint(manifest), manifest['id'])
            other = copy.deepcopy(ranged)
            other['compatibility']['core'] = '<1'
            self.assertEqual(host.fingerprint(other), host.fingerprint(manifest))

    def test_compatibility_keys_stay_strict(self):
        target = self.root / 'check' / 'demo-sidebar'
        target.mkdir(parents=True)
        for compat in ({'host_api': 2, 'core': '>1'}, {'host_api': 2, 'core': None}, {'host_api': 2, 'extra': 1},
                       {'core': '>=1'}, {'host_api': 3}, {'host_api': '2'}):
            manifest = sidebar_manifest()
            manifest['compatibility'] = compat
            (target / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError, msg=repr(compat)):
                host._manifest(target, 'demo-sidebar')

    def incompatible_bundled(self):
        manifest = sidebar_manifest('demo-bundled', core='>=99')
        (self.bundled / 'demo-bundled/manifest.json').write_text(json.dumps(manifest))
        self.write_config({'demo-bundled': {'enabled': True}})
        host.grant(self.config, manifest, host.fingerprint(manifest), 'test')
        return manifest

    def test_incompatible_bundled_plugin_is_inert_and_keeps_its_grant(self):
        self.incompatible_bundled()
        item = self.item('demo-bundled')
        self.assertEqual(item['status'], 'incompatible')
        self.assertEqual(item['incompatible'], {'core': __version__, 'requires': '>=99'})
        self.assertEqual(item['compatibility'], {'host_api': 2, 'core': '>=99'})
        self.assertEqual(item['consent']['status'], 'granted')
        self.assertEqual(host.page_plugins(self.config), [])
        self.assertEqual(host.sidebar_plugins(self.config), [])
        with self.assertRaises(agent_api.APIError) as error:
            plugin_pages.require_page(self.config, 'demo-bundled')
        self.assertEqual(error.exception.status, 403)
        self.assertTrue((host._root(self.config) / 'demo-bundled' / 'consent.json').exists())
        code, out, _ = self.cli('list')
        self.assertIn(f'demo-bundled  1.0.0  builtin  incompatible  (requires board >=99, this board is {__version__})', out)
        # It cannot be switched on; disabling and revoking keep working.
        editor = ConfigEditor(self.config, self.config_path)
        digest = editor.read_plugins()['digest']
        with self.assertRaises(ConfigEditError) as error:
            editor.save_plugin({'digest': digest, 'id': 'demo-bundled', 'enabled': True})
        self.assertEqual(error.exception.code, 'validation_failed')
        editor.save_plugin({'digest': digest, 'id': 'demo-bundled', 'enabled': False})
        self.assertEqual(self.cli('revoke', 'demo-bundled')[0], 0)

    def test_incompatible_hook_is_never_dispatched(self):
        manifest = hook_manifest('demo-hook', core='<1.0')
        directory = write_plugin(self.installed_root(), manifest)
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, manifest, host.fingerprint(manifest), 'test')
        self.assertEqual(self.item('demo-hook')['status'], 'incompatible')
        with patch.object(host, '_run_worker', side_effect=AssertionError('dispatched')):
            host.dispatch(self.config, 'after_analyze_success', [{'uid': 'synthetic:1', 'company': 'A'}], 1)
        self.assertEqual(host.read_debug(self.config, 'demo-hook')['entries'], [])
        # A compatible range runs again with the same grant.
        manifest['compatibility']['core'] = '>=1.0'
        (directory / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(self.item('demo-hook')['status'], 'enabled')


class StatusSurface(Base):
    def test_bundled_items_gain_only_the_additive_fields(self):
        item = self.item('demo-bundled')
        self.assertEqual(set(item), {'id', 'name', 'version', 'enabled', 'description', 'status', 'error_count',
                                     'consent', 'origin', 'compatibility'})
        self.assertEqual((item['origin'], item['compatibility']), ('builtin', {'host_api': 2, 'core': None}))
        (self.bundled / 'demo-bundled/manifest.json').write_text('{broken')
        item = self.item('demo-bundled')
        self.assertEqual((item['status'], item['origin'], item['compatibility']), ('error', 'builtin', None))
        self.assertNotIn('install', item)

    def test_shipped_plugins_list_unchanged_apart_from_new_fields(self):
        with patch.object(host, 'PLUGIN_ROOT', ROOT / 'plugins'):
            items = host.list_plugins(self.config)
        self.assertEqual([i['id'] for i in items], ['map', 'sponsorship', 'workspace'])
        for item in items:
            self.assertEqual(item['origin'], 'builtin')
            self.assertIsNone(item['compatibility']['core'])
            self.assertNotIn('install', item)
            self.assertNotIn('incompatible', item)

    def test_agent_api_passes_the_fields_through(self):
        path, _ = self.build(sidebar_manifest())
        self.assertEqual(self.cli('install', str(path), '--yes')[0], 0)
        api = agent_api.AgentAPI(self.config, config_path=self.config_path)
        plugins = {p['id']: p for p in api.plugins()['plugins']}
        self.assertEqual(plugins['demo-sidebar']['origin'], 'installed')
        self.assertEqual(plugins['demo-sidebar']['install'], self.item('demo-sidebar')['install'])
        self.assertEqual(plugins['demo-bundled']['compatibility'], {'host_api': 2, 'core': None})


class InstalledPluginsRun(Base):
    """An installed package is served and dispatched exactly like a bundled one."""

    def install(self, manifest, **kwargs):
        path, _ = self.build(manifest, **kwargs)
        code, _, err = self.cli('install', str(path), '--yes')
        self.assertEqual(code, 0, err)
        return host.packages_root(self.config) / manifest['id']

    def test_installed_python_hook_dispatches_with_its_own_file_scope(self):
        outside = self.root / 'outside.txt'
        outside.write_text('private')
        code = ("from pathlib import Path\n"
                "def after_analyze_success(r):\n"
                " own=(Path(__file__).parent/'payload.txt').read_text()\n"
                " try:\n"
                f"  open({str(outside)!r}).read(); seen='read'\n"
                " except PermissionError:\n"
                "  seen='denied'\n"
                " v={'badges':[{'text':own+':'+seen,'level':'info','tooltip':'t'}],'sections':[]}\n"
                " return {'analysis_id':r['analysis_id'],'results':{j['uid']:{'zh':v,'en':v} for j in r['jobs']}}\n")
        directory = self.install(hook_manifest(), code=code, extra={'payload.txt': 'from-package'})
        with closing(sqlite3.connect(self.config['db_path'])) as conn, conn:
            conn.execute('CREATE TABLE runs (run_id INTEGER PRIMARY KEY, kind TEXT)')
            conn.execute("INSERT INTO runs VALUES (1, 'analyze')")
        self.write_config({'demo-hook': {'enabled': True}})
        host.grant(self.config, hook_manifest(), host.fingerprint(hook_manifest()), 'test')
        self.assertEqual(host.plugin_dir(self.config, 'demo-hook'), directory)
        host.dispatch(self.config, 'after_analyze_success', [{'uid': 'synthetic:1', 'company': 'A'}], 1)
        self.assertEqual(host.read_debug(self.config, 'demo-hook')['entries'], [])
        self.assertEqual(host.presentation(self.config, 'synthetic:1')['badges'][0]['text'], 'from-package:denied')

    def test_installed_sidebar_is_served_like_a_bundled_one(self):
        self.install(sidebar_manifest())
        config = self.config
        with closing(db.connect(config['db_path'])) as conn, conn:
            db.upsert_job(conn, {'source': 'synthetic', 'source_id': 'one', 'title': 'Synthetic engineer',
                                 'company': 'Synthetic Company', 'location': 'Melbourne VIC 3000',
                                 'description': 'Synthetic description.', 'url': 'https://example.invalid/one'})
            db.replace_decision(conn, 'synthetic:one', False)
            db.replace_score(conn, 'synthetic:one', 'T1', 3, 0, 0, 10, '[]', 10)
            db.log_run(conn, 'analyze', db.utcnow(), {})
        (self.root / 'skills.yaml').write_text('version: 1\nskills: []\n')
        config.update(skills_path=str(self.root / 'skills.yaml'), queries=['Synthetic'])
        handler = type('InstalledHandler', (panel.Handler,), {'config': config})
        server = panel.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown(); server.server_close(); thread.join(3)
        self.addCleanup(stop)
        origin = 'http://127.0.0.1:' + str(server.server_port)

        def get(path):
            client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                client.request('GET', path, headers={'Origin': origin})
                response = client.getresponse()
                return response.status, response.read()
            finally:
                client.close()
        self.assertEqual(get('/plugins/demo-sidebar/frame?slot=sidebar')[0], 403)
        config['plugins'] = {'demo-sidebar': {'enabled': True}}
        host.grant(config, sidebar_manifest(), host.fingerprint(sidebar_manifest()), 'test')
        status, body = get('/plugins/demo-sidebar/frame?slot=sidebar')
        self.assertEqual((status, body), (200, b'<p>synthetic sidebar 1.0.0</p>'))
        self.assertEqual(get('/plugins/demo-sidebar/frame?slot=page'), (200, b'<p>synthetic compare</p>'))
        self.assertEqual(get('/plugins/demo-sidebar/assets/app.js'), (200, b'// synthetic asset'))
        self.assertEqual(get('/plugins/demo-sidebar/assets/../manifest.json')[0], 404)
        status, body = get('/api/plugin-view/demo-sidebar/handed?uid=synthetic:one')
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)['items'][0]['title'], 'Synthetic engineer')
        self.assertEqual([s['id'] for s in host.sidebar_plugins(config)], ['demo-sidebar'])
        self.assertEqual([p['id'] for p in host.page_plugins(config)], ['demo-sidebar'])
        # An incompatible installed plugin gets the ordinary 403, its grant kept.
        manifest = sidebar_manifest(core='<1.0')
        (host.packages_root(config) / 'demo-sidebar/manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(get('/plugins/demo-sidebar/frame?slot=sidebar')[0], 403)
        self.assertEqual(get('/plugins/demo-sidebar/assets/app.js')[0], 403)
        self.assertEqual(get('/api/plugin-view/demo-sidebar/handed?uid=synthetic:one')[0], 403)
        self.assertEqual(host.sidebar_plugins(config), [])
        self.assertTrue((host._root(config) / 'demo-sidebar' / 'consent.json').exists())


if __name__ == '__main__':
    unittest.main()
