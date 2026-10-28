"""Synthetic security boundary checks; native ACL cases run on Windows only."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import file_security as security, agent_api, agent_client


def grant_public_read(path):
    """Deliberately unsafe synthetic permission fixture for native rejection."""
    if os.name != 'nt':
        Path(path).chmod(0o644)
        return
    native = security._WindowsSecurity()
    c = native.c
    sd, acl = c.c_void_p(), c.c_void_p()
    present, defaulted = native.w.BOOL(), native.w.BOOL()
    try:
        native._check(native.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f'D:P(A;;FA;;;{native._sid()})(A;;FR;;;WD)', 1, c.byref(sd), None))
        native._check(native.a.GetSecurityDescriptorDacl(sd, c.byref(present), c.byref(acl), c.byref(defaulted)))
        result = native.a.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None)
        if result: raise c.WinError(result)
    finally:
        if sd: native.k.LocalFree(sd)


class FileSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='security-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'token'
        self.path.write_text('x' * 32, encoding='ascii')

    def test_private_token_and_rejections_are_shared(self):
        security.set_private(self.path)
        security.validate_private_file(self.path)
        self.assertEqual(agent_api.read_token(self.path), 'x' * 32)
        self.assertEqual(agent_client.read_token(self.path), 'x' * 32)
        self.path.write_text('x' * 4098)
        for reader, error in ((agent_api.read_token, ValueError), (agent_client.read_token, agent_client.ClientError)):
            with self.assertRaises(error): reader(self.path)
        with self.assertRaises(ValueError): security.validate_private_file(self.path.parent)

    @unittest.skipIf(os.name == 'nt', 'POSIX mode regression')
    def test_posix_mode_and_reject_broad_permissions(self):
        security.set_private(self.path)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        with self.path.open('rb') as f:
            security.apply_file_mode(f, 0o640)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)
        with self.assertRaises(ValueError): security.validate_private_file(self.path)

    def test_windows_dispatch_and_errors_fail_closed(self):
        class FakeHandle:
            name = 'synthetic-path'
        with patch.object(security, '_is_windows', return_value=True), patch.object(security, 'set_private') as private:
            security.apply_file_mode(FakeHandle(), 0o644)
            private.assert_called_once_with('synthetic-path')
        with patch.object(security, '_WindowsSecurity') as native:
            with patch.object(security, '_is_windows', return_value=True):
                security.set_private('synthetic-path')
                native.return_value.set_private.assert_called_once_with('synthetic-path')
                native.return_value.set_private.side_effect = OSError('ACL failure')
                with self.assertRaises(OSError): security.set_private('synthetic-path')

    def test_windows_validation_dispatch_rejects_bad_acl_for_both_readers(self):
        with patch.object(security, '_is_windows', return_value=True), patch.object(security, '_WindowsSecurity') as native:
            self.assertEqual(agent_api.read_token(self.path), 'x' * 32)
            self.assertEqual(agent_client.read_token(self.path), 'x' * 32)
            self.assertEqual(native.return_value.validate.call_count, 2)
            native.return_value.validate.side_effect = ValueError('bad ACL')
            with self.assertRaises(ValueError): agent_api.read_token(self.path)
            with self.assertRaises(agent_client.ClientError): agent_client.read_token(self.path)
            native.return_value.validate.side_effect = OSError('cannot inspect ACL')
            with self.assertRaises(OSError): agent_api.read_token(self.path)
            with self.assertRaises(OSError): agent_client.read_token(self.path)

    @unittest.skipUnless(os.name == 'nt', 'requires native Windows ACLs')
    def test_native_windows_inheritance_and_extra_principal(self):
        security.set_private(self.path)
        security.validate_private_file(self.path)
        native = security._WindowsSecurity()
        c = native.c
        sd, acl, present, defaulted = c.c_void_p(), c.c_void_p(), native.w.BOOL(), native.w.BOOL()
        try:
            # Deliberately broad ACL: Everyone can read. Protected or not, it must be rejected.
            native._check(native.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f'D:P(A;;FA;;;{native._sid()})(A;;FR;;;WD)', 1, c.byref(sd), None))
            native._check(native.a.GetSecurityDescriptorDacl(sd, c.byref(present), c.byref(acl), c.byref(defaulted)))
            result = native.a.SetNamedSecurityInfoW(str(self.path), 1, 0x80000004, None, None, acl, None)
            if result: raise c.WinError(result)
            for reader, error in ((agent_api.read_token, ValueError), (agent_client.read_token, agent_client.ClientError)):
                with self.assertRaises(error): reader(self.path)
        finally:
            if sd: native.k.LocalFree(sd)
        security.set_private(self.path)
        self.assertEqual(agent_client.read_token(self.path), 'x' * 32)


if __name__ == '__main__': unittest.main()
