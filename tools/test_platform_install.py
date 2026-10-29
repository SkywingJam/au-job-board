"""Synthetic source-install checks for byte resources, dates and Unicode paths."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jobs import agent_api, locations


class InstallTests(unittest.TestCase):
    def test_melbourne_day_and_dst_boundaries(self):
        for day, hours in [('2026-10-04', 23), ('2026-04-05', 25), ('2026-10-28', 24)]:
            first, last = agent_api.day_window(day)
            self.assertEqual(last - first, dt.timedelta(hours=hours))

    def test_reference_hash_and_autocrlf_checkout(self):
        metadata = json.loads((ROOT / 'jobs/data/location-reference.json').read_text(encoding='utf-8'))
        source = ROOT / 'jobs/data/au-postcodes.tsv'
        # This checkout itself may predate the LF attribute and still hold CRLF.
        data = source.read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(data).hexdigest(), metadata['seed']['sha256'])
        locations._reference()
        # A tiny isolated repo exercises real Git conversion without touching
        # the user's index, global configuration or source checkout.
        with tempfile.TemporaryDirectory(prefix='windows-checkout-') as temp:
            root = Path(temp)
            def git(*args):
                return subprocess.run(['git', '-c', 'core.autocrlf=true', *args], cwd=root,
                                      capture_output=True, check=True)
            git('init', '-q')
            (root / '.gitattributes').write_bytes((ROOT / '.gitattributes').read_bytes())
            target = root / 'jobs/data/au-postcodes.tsv'
            target.parent.mkdir(parents=True)
            target.write_bytes(data)
            git('add', '.gitattributes', 'jobs/data/au-postcodes.tsv')
            target.unlink()
            git('checkout-index', '-f', '--', 'jobs/data/au-postcodes.tsv')
            self.assertEqual(target.read_bytes(), data)

    def test_reference_accepts_crlf_left_by_an_older_windows_checkout(self):
        # Clone before the LF attribute, then update: Git keeps the CRLF working file.
        data = (ROOT / 'jobs/data/au-postcodes.tsv').read_bytes().replace(b'\r\n', b'\n')
        crlf = data.replace(b'\n', b'\r\n')
        with tempfile.TemporaryDirectory(prefix='crlf-reference-') as temp:
            folder = Path(temp)
            (folder / 'location-reference.json').write_bytes(
                (ROOT / 'jobs/data/location-reference.json').read_bytes())
            for name, payload, valid in (('crlf', crlf, True), ('changed', crlf.replace(b'3053', b'3054', 1), False)):
                with self.subTest(name):
                    (folder / 'au-postcodes.tsv').write_bytes(payload)
                    locations._reference.cache_clear()
                    with unittest.mock.patch.object(locations, '_DATA', folder):
                        if valid:
                            self.assertIn('3053', locations._reference()[2])
                        else:
                            with self.assertRaises(ValueError):
                                locations._reference()
        locations._reference.cache_clear()

    def test_readonly_cli_from_unicode_space_directory(self):
        with tempfile.TemporaryDirectory(prefix='jobs 中文 path ') as temp:
            path = Path(temp) / 'public example.yaml'
            path.write_bytes((ROOT / 'config.example.yaml').read_bytes())
            result = subprocess.run([sys.executable, '-m', 'jobs.cli', '--config', str(path),
                                     'eligibility-profile'], cwd=temp, capture_output=True, text=True,
                                    encoding='utf-8', timeout=20,
                                    env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHONUTF8': '1'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse((Path(temp) / 'data').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
