"""Private files: POSIX modes and a restricted Windows NTFS DACL.

Validation is deliberately conservative: unsupported ACEs and unexpected owners
are rejected. OSError means the operating system could not establish or inspect
security; ValueError means an inspected file is not private.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


def _is_windows():
    return os.name == 'nt'


def set_private(path):
    if _is_windows():
        _WindowsSecurity().set_private(path)
    else:
        os.chmod(path, 0o600)


def apply_file_mode(handle, mode):
    if _is_windows():
        set_private(handle.name)
    else:
        os.fchmod(handle.fileno(), mode)


def validate_private_file(path):
    """Return None for a small private regular file; raise on rejection."""
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 4097:
        raise ValueError('not a small regular file')
    if _is_windows():
        _WindowsSecurity().validate(path)
    elif info.st_mode & 0o077:
        raise ValueError('file is accessible to others')


class _WindowsSecurity:
    """Small wrapper around Windows security descriptors; loaded only on Windows."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.c = ctypes
        self.w = wintypes
        self.a = ctypes.WinDLL('advapi32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        p = ctypes.c_void_p
        d = wintypes.DWORD
        b = wintypes.BOOL
        def bind(lib, name, args, result):
            fn = getattr(lib, name)
            fn.argtypes, fn.restype = args, result
        bind(self.k, 'GetCurrentProcess', [], p)
        bind(self.k, 'CloseHandle', [p], b)
        bind(self.k, 'LocalFree', [p], p)
        bind(self.a, 'OpenProcessToken', [p, d, ctypes.POINTER(p)], b)
        bind(self.a, 'GetTokenInformation', [p, d, p, d, ctypes.POINTER(d)], b)
        bind(self.a, 'ConvertSidToStringSidW', [p, ctypes.POINTER(p)], b)
        bind(self.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [wintypes.LPCWSTR, d, ctypes.POINTER(p), ctypes.POINTER(d)], b)
        bind(self.a, 'GetSecurityDescriptorDacl', [p, ctypes.POINTER(b), ctypes.POINTER(p), ctypes.POINTER(b)], b)
        bind(self.a, 'SetNamedSecurityInfoW', [wintypes.LPWSTR, d, d, p, p, p, p], d)
        bind(self.a, 'GetNamedSecurityInfoW', [wintypes.LPWSTR, d, d, ctypes.POINTER(p), p, ctypes.POINTER(p), p, ctypes.POINTER(p)], d)
        bind(self.a, 'GetSecurityDescriptorControl', [p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(d)], b)
        bind(self.a, 'GetAce', [p, d, ctypes.POINTER(p)], b)
        bind(self.a, 'EqualSid', [p, p], b)
        bind(self.a, 'ConvertStringSidToSidW', [wintypes.LPCWSTR, ctypes.POINTER(p)], b)

    def _check(self, result):
        if not result:
            raise self.c.WinError(self.c.get_last_error())

    def _sid(self):
        c, w = self.c, self.w
        token = c.c_void_p()
        self._check(self.a.OpenProcessToken(self.k.GetCurrentProcess(), 8, c.byref(token)))
        try:
            size = w.DWORD()
            self.a.GetTokenInformation(token, 1, None, 0, c.byref(size))
            if not size.value:
                raise c.WinError(c.get_last_error())
            buf = c.create_string_buffer(size.value)
            self._check(self.a.GetTokenInformation(token, 1, buf, size, c.byref(size)))
            sid = c.cast(buf, c.POINTER(c.c_void_p))[0]
            string = c.c_void_p()
            self._check(self.a.ConvertSidToStringSidW(sid, c.byref(string)))
            try:
                return c.wstring_at(string)
            finally:
                self.k.LocalFree(string)
        finally:
            self.k.CloseHandle(token)

    def _sid_ptr(self, value):
        ptr = self.c.c_void_p()
        self._check(self.a.ConvertStringSidToSidW(value, self.c.byref(ptr)))
        return ptr

    def set_private(self, path):
        c, w = self.c, self.w
        user = self._sid()
        sd, owner = c.c_void_p(), self._sid_ptr(user)
        try:
            sddl = f'D:P(A;;FA;;;{user})(A;;FA;;;SY)'
            self._check(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, c.byref(sd), None))
            present, defaulted, dacl = w.BOOL(), w.BOOL(), c.c_void_p()
            self._check(self.a.GetSecurityDescriptorDacl(sd, c.byref(present), c.byref(dacl), c.byref(defaulted)))
            # Set owner too: an unexpected owner can change the DACL implicitly.
            result = self.a.SetNamedSecurityInfoW(os.fspath(path), 1, 0x80000005, owner, None, dacl, None)
            if result:
                raise c.WinError(result)
        finally:
            if sd: self.k.LocalFree(sd)
            self.k.LocalFree(owner)
        self.validate(path)

    def validate(self, path):
        c, w = self.c, self.w
        owner, dacl, sd = c.c_void_p(), c.c_void_p(), c.c_void_p()
        user = self._sid_ptr(self._sid())
        system = None
        try:
            system = self._sid_ptr('S-1-5-18')
            result = self.a.GetNamedSecurityInfoW(os.fspath(path), 1, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(sd))
            if result:
                raise c.WinError(result)
            control, revision = w.WORD(), w.DWORD()
            self._check(self.a.GetSecurityDescriptorControl(sd, c.byref(control), c.byref(revision)))
            if not owner or not dacl or not (self.a.EqualSid(owner, user) or self.a.EqualSid(owner, system)):
                raise ValueError('missing DACL or unexpected owner')
            # Check every ACE, including inherited entries. Future inheritance
            # changes are rechecked on each open; unknown ACE types fail closed.
            count = c.c_ushort.from_address(dacl.value + 4).value
            for index in range(count):
                ace = c.c_void_p()
                self._check(self.a.GetAce(dacl, index, c.byref(ace)))
                header = (c.c_ubyte * 4).from_address(ace.value)
                if header[0] != 0 or int.from_bytes(bytes(header[2:4]), 'little') < 16:
                    raise ValueError('unsupported ACE')
                sid = c.c_void_p(ace.value + 8)
                if not (self.a.EqualSid(sid, user) or self.a.EqualSid(sid, system)):
                    raise ValueError('unexpected ACL principal')
        finally:
            if sd: self.k.LocalFree(sd)
            if system: self.k.LocalFree(system)
            self.k.LocalFree(user)
