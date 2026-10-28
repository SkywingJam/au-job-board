"""Windows-only guarded Python workers and bounded process-tree cleanup.

CreateProcess associates each child with a kill-on-close Job Object atomically,
before even a venv redirector can create descendants. Only the lock and standard
streams are inherited. This requires Windows 10 / Server 2016 or newer.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

_INHERITANCE_LOCK = threading.Lock()


def inherited_lock(raw):
    import msvcrt
    fd = msvcrt.open_osfhandle(raw, os.O_RDWR | os.O_BINARY)
    os.set_inheritable(fd, False)
    return os.fdopen(fd, 'r+b')


def lock_handle(lock):
    import msvcrt
    return msvcrt.get_osfhandle(lock.fileno())


class WindowsJob:
    """Own a process tree; fail closed if Windows cannot contain the child."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        class BasicLimits(c.Structure):
            _fields_ = [('PerProcessUserTimeLimit', c.c_longlong),
                        ('PerJobUserTimeLimit', c.c_longlong), ('LimitFlags', w.DWORD),
                        ('MinimumWorkingSetSize', c.c_size_t), ('MaximumWorkingSetSize', c.c_size_t),
                        ('ActiveProcessLimit', w.DWORD), ('Affinity', c.c_size_t),
                        ('PriorityClass', w.DWORD), ('SchedulingClass', w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(n, c.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount',
                         'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Limits(c.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IO),
                        ('ProcessMemoryLimit', c.c_size_t), ('JobMemoryLimit', c.c_size_t),
                        ('PeakProcessMemoryUsed', c.c_size_t), ('PeakJobMemoryUsed', c.c_size_t)]
        class Accounting(c.Structure):
            _fields_ = [(n, c.c_longlong) for n in ('TotalUserTime', 'TotalKernelTime',
                         'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')] + [
                         (n, w.DWORD) for n in ('TotalPageFaultCount', 'TotalProcesses',
                                                'ActiveProcesses', 'TotalTerminatedProcesses')]
        self.Accounting = Accounting
        for name, args, result in (
            ('CreateJobObjectW', [w.LPVOID, w.LPCWSTR], w.HANDLE),
            ('SetInformationJobObject', [w.HANDLE, c.c_int, w.LPVOID, w.DWORD], w.BOOL),
            ('QueryInformationJobObject', [w.HANDLE, c.c_int, w.LPVOID, w.DWORD, w.LPVOID], w.BOOL),
            ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
            ('CloseHandle', [w.HANDLE], w.BOOL)):
            fn = getattr(self.k, name)
            fn.argtypes, fn.restype = args, result
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Limits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway.
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.WinError(c.get_last_error())
            self.close()
            raise error

    def stop(self, process):
        """Return only once every job member has exited, before releasing locks."""
        if not self.k.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())
        while True:
            info = self.Accounting()
            if not self.k.QueryInformationJobObject(self.handle, 1, self.c.byref(info),
                                                     self.c.sizeof(info), None):
                raise self.c.WinError(self.c.get_last_error())
            if not info.ActiveProcesses:
                break
            time.sleep(0.01)
        process.wait()

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


class WindowsProcess:
    """Minimal owned process handle used by the Windows job supervisor."""
    def __init__(self, kernel, handle, pid, args):
        self.k, self._handle, self.pid, self.args = kernel, handle, pid, args
        self.returncode = None

    def poll(self):
        try:
            return self.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return None

    def wait(self, timeout=None):
        import ctypes as c
        from ctypes import wintypes as w
        if self.returncode is not None:
            return self.returncode
        milliseconds = 0xffffffff if timeout is None else max(0, int(timeout * 1000))
        result = self.k.WaitForSingleObject(self._handle, milliseconds)
        if result == 258:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != 0:
            raise c.WinError(c.get_last_error())
        code = w.DWORD()
        if not self.k.GetExitCodeProcess(self._handle, c.byref(code)):
            raise c.WinError(c.get_last_error())
        self.returncode = code.value
        return self.returncode

    def kill(self):
        import ctypes as c
        if self.poll() is None and not self.k.TerminateProcess(self._handle, 1):
            raise c.WinError(c.get_last_error())

    def close(self):
        if self._handle:
            self.k.CloseHandle(self._handle)
            self._handle = None


def _create_in_job(job, command, raw_lock, *, cwd=None, stdin=None, stdout=None, stderr=None):
    """Native STARTUPINFOEX: atomic job membership plus an explicit handle list.

    subprocess's Python 3.12 attribute dictionary only supports handle_list.
    Calling CreateProcessW here also preserves venv selection and quoting.
    """
    c, w, k = job.c, job.w, job.k
    class Startup(c.Structure):
        _fields_ = [('cb', w.DWORD), ('lpReserved', w.LPWSTR), ('lpDesktop', w.LPWSTR),
                    ('lpTitle', w.LPWSTR), ('dwX', w.DWORD), ('dwY', w.DWORD),
                    ('dwXSize', w.DWORD), ('dwYSize', w.DWORD), ('dwXCountChars', w.DWORD),
                    ('dwYCountChars', w.DWORD), ('dwFillAttribute', w.DWORD),
                    ('dwFlags', w.DWORD), ('wShowWindow', w.WORD), ('cbReserved2', w.WORD),
                    ('lpReserved2', c.POINTER(c.c_ubyte)), ('hStdInput', w.HANDLE),
                    ('hStdOutput', w.HANDLE), ('hStdError', w.HANDLE)]
    class StartupEx(c.Structure):
        _fields_ = [('StartupInfo', Startup), ('lpAttributeList', w.LPVOID)]
    class ProcessInfo(c.Structure):
        _fields_ = [('hProcess', w.HANDLE), ('hThread', w.HANDLE),
                    ('dwProcessId', w.DWORD), ('dwThreadId', w.DWORD)]
    for name, args, result in (
        ('GetCurrentProcess', [], w.HANDLE),
        ('GetStdHandle', [w.DWORD], w.HANDLE),
        ('DuplicateHandle', [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD], w.BOOL),
        ('InitializeProcThreadAttributeList', [w.LPVOID, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)], w.BOOL),
        ('UpdateProcThreadAttribute', [w.LPVOID, w.DWORD, c.c_size_t, w.LPVOID, c.c_size_t, w.LPVOID, w.LPVOID], w.BOOL),
        ('DeleteProcThreadAttributeList', [w.LPVOID], None),
        ('CreateProcessW', [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD,
                            w.LPVOID, w.LPCWSTR, w.LPVOID, c.POINTER(ProcessInfo)], w.BOOL),
        ('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
        ('GetExitCodeProcess', [w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        ('TerminateProcess', [w.HANDLE, w.UINT], w.BOOL)):
        fn = getattr(k, name)
        fn.argtypes, fn.restype = args, result
    import msvcrt
    from contextlib import ExitStack
    with ExitStack() as cleanup:
        startup = StartupEx()
        startup.StartupInfo.cb = c.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
        handles = [raw_lock]
        current = k.GetCurrentProcess()
        for name, value, identifier in (('hStdInput', stdin, -10), ('hStdOutput', stdout, -11),
                                         ('hStdError', stderr, -12)):
            if value not in (None, subprocess.DEVNULL):
                raise ValueError('Windows worker streams must be inherited or DEVNULL')
            source = k.GetStdHandle(identifier & 0xffffffff) if value is None else None
            if source in (None, 0, c.c_void_p(-1).value):
                fd = os.open(os.devnull, os.O_RDWR | os.O_BINARY)
                cleanup.callback(os.close, fd)
                source = msvcrt.get_osfhandle(fd)
            duplicate = w.HANDLE()
            if not k.DuplicateHandle(current, source, current, c.byref(duplicate), 0, True, 2):
                raise c.WinError(c.get_last_error())
            cleanup.callback(k.CloseHandle, duplicate.value)
            handles.append(duplicate.value)
            setattr(startup.StartupInfo, name, duplicate.value)
        size = c.c_size_t()
        k.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
        if not size.value:
            raise c.WinError(c.get_last_error())
        attributes = c.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)):
            raise c.WinError(c.get_last_error())
        cleanup.callback(k.DeleteProcThreadAttributeList, attributes)
        startup.lpAttributeList = c.cast(attributes, w.LPVOID)
        # Keep attribute values alive through CreateProcessW. JOB_LIST performs
        # assignment before the venv launcher or interpreter executes any code.
        handle_list = (w.HANDLE * len(handles))(*handles)
        job_list = (w.HANDLE * 1)(job.handle)
        for attribute, values in ((0x20002, handle_list), (0x2000d, job_list)):
            if not k.UpdateProcThreadAttribute(attributes, 0, attribute, values, c.sizeof(values), None, None):
                raise c.WinError(c.get_last_error())
        info = ProcessInfo()
        # list2cmdline applies the same MS C-runtime quoting used by Popen.
        text = c.create_unicode_buffer(subprocess.list2cmdline(command))
        flags = 0x80000 | 0x200  # EXTENDED_STARTUPINFO_PRESENT | CREATE_NEW_PROCESS_GROUP
        if not k.CreateProcessW(command[0], text, None, None, True, flags, None,
                                os.fspath(cwd) if cwd is not None else None,
                                c.byref(startup), c.byref(info)):
            raise c.WinError(c.get_last_error())
        k.CloseHandle(info.hThread)
        return WindowsProcess(k, info.hProcess, info.dwProcessId, command)


def spawn_python(arguments, lock, **options):
    """Start a Python tree inside its job at creation, or fail without a worker."""
    job = WindowsJob()
    process = None
    try:
        raw_lock = lock_handle(lock)
        command = [sys.executable, '-m', 'jobs.cli', *arguments, '--run-lock-handle', str(raw_lock)]
        # Serialize temporarily inheritable lock handles. The native call's
        # explicit handle list excludes every unrelated inheritable handle.
        with _INHERITANCE_LOCK:
            try:
                os.set_handle_inheritable(raw_lock, True)
                process = _create_in_job(job, command, raw_lock, **options)
            finally:
                os.set_handle_inheritable(raw_lock, False)
        return process, job
    except BaseException:
        try:
            if process is not None:
                job.stop(process)
        finally:
            job.close()
            if process is not None:
                process.close()
        raise
