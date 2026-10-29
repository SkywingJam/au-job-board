"""Persistent cross-process file locks shared by runtime and skills writers."""
from __future__ import annotations

import os
import time
from pathlib import Path


def acquire_file_lock(path, *, blocking=False, mode=0o666):
    """Return a locked file handle, or None on nonblocking contention.

    Lock files have stable identities and must not be unlinked by callers.
    On Windows the open handle denies all sharing and can be inherited by a
    child; the exclusion remains until the last duplicate is closed.
    """
    path = Path(path)
    if os.name != 'nt':
        import fcntl
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, mode)
        handle = os.fdopen(descriptor, 'a+')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            handle.close()
            return None
        except BaseException:
            handle.close()
            raise
        return handle

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    while True:
        raw = create(str(path), 0x80000000 | 0x40000000, 0, None, 4, 0x80, None)
        if raw != ctypes.c_void_p(-1).value:
            try:
                fd = msvcrt.open_osfhandle(raw, os.O_RDWR | os.O_BINARY)
            except BaseException:
                close(raw)
                raise
            try:
                return os.fdopen(fd, 'r+b')
            except BaseException:
                os.close(fd)
                raise
        error = ctypes.get_last_error()
        if error not in (32, 33):
            raise ctypes.WinError(error)
        if not blocking:
            return None
        time.sleep(0.05)
