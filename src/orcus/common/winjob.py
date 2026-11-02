"""Job Object, engine ownership lock and process inspection (ARCHITECTURE.md §1.2, §1.3, §5.7).

Rules kept here:
- KILL_ON_JOB_CLOSE, no BREAKAWAY_OK by default, handle not inheritable.
- TerminateJobObject is never called: our own process is in the job. Forced stops kill by PID.
"""

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .win32 import INVALID_HANDLE_VALUE, close_handle, kernel32

JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9

PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x102
STILL_ACTIVE = 259

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x80
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
TH32CS_SNAPPROCESS = 0x2


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE


def _check(ok: object) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class Job:
    """A Job Object owned by this process. Closing the handle kills members when kill_on_close."""

    def __init__(self, handle: int) -> None:
        self.handle = handle

    @classmethod
    def create(cls, *, kill_on_close: bool, breakaway_ok: bool = False) -> "Job":
        handle = kernel32.CreateJobObjectW(None, None)
        _check(handle)
        flags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0) | \
                (JOB_OBJECT_LIMIT_BREAKAWAY_OK if breakaway_ok else 0)
        if flags:
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = flags
            ok = kernel32.SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                                                  ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                err = ctypes.get_last_error()
                close_handle(handle)
                raise ctypes.WinError(err)
        return cls(handle)

    def assign(self, process_handle: int) -> None:
        _check(kernel32.AssignProcessToJobObject(self.handle, process_handle))

    def assign_current(self) -> None:
        self.assign(kernel32.GetCurrentProcess())

    def contains(self, process_handle: int) -> bool:
        return is_process_in_job(process_handle, self.handle)

    def process_ids(self) -> list[int]:
        for capacity in (64, 1024, 16384):
            class IdList(ctypes.Structure):
                _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD),
                            ("NumberOfProcessIdsInList", wintypes.DWORD),
                            ("ProcessIdList", ctypes.c_size_t * capacity)]
            buf = IdList()
            ok = kernel32.QueryInformationJobObject(self.handle, JobObjectBasicProcessIdList,
                                                    ctypes.byref(buf), ctypes.sizeof(buf), None)
            if ok or buf.NumberOfAssignedProcesses <= capacity:
                _check(ok)
                return [int(buf.ProcessIdList[i]) for i in range(buf.NumberOfProcessIdsInList)]
        raise RuntimeError("too many processes in job")

    def close(self) -> None:
        close_handle(self.handle)
        self.handle = 0


def is_process_in_job(process_handle: int, job_handle: int | None = None) -> bool:
    """job_handle None = in any job."""
    result = wintypes.BOOL()
    _check(kernel32.IsProcessInJob(process_handle, job_handle, ctypes.byref(result)))
    return bool(result.value)


def pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False   # gone, or not ours to see; callers only ask about our own children
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        close_handle(handle)


def terminate_pid(pid: int, exit_code: int = 1) -> bool:
    handle = kernel32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        ok = bool(kernel32.TerminateProcess(handle, exit_code))
        kernel32.WaitForSingleObject(handle, 5000)
        return ok
    finally:
        close_handle(handle)


def image_path(pid: int) -> str | None:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        close_handle(handle)


@dataclass(frozen=True)
class ProcEntry:
    pid: int
    ppid: int
    exe: str


def list_processes() -> list[ProcEntry]:
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        result: list[ProcEntry] = []
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            result.append(ProcEntry(int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return result
    finally:
        close_handle(snap)


def processes_under(root: Path) -> list[tuple[int, str]]:
    """(pid, image) of every process whose image lives under `root` (e.g. D:\\OrcusAPK\\sdk)."""
    prefix = str(root).rstrip("\\").lower() + "\\"
    found: list[tuple[int, str]] = []
    for p in list_processes():
        if p.pid in (0, 4, os.getpid()):
            continue
        image = image_path(p.pid)
        if image and image.lower().startswith(prefix):
            found.append((p.pid, image))
    return found


class LockBusy(Exception):
    """Another process holds the lock file."""


class ExclusiveLock:
    """Exclusive open (share mode 0) of a lock file, held for the VM's lifetime.

    The OS releases it when the process dies.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: int | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = kernel32.CreateFileW(str(self.path), GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_ALWAYS,
                                      FILE_ATTRIBUTE_NORMAL, None)
        if not handle or handle == INVALID_HANDLE_VALUE:
            err = ctypes.get_last_error()
            if err in (ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION):
                raise LockBusy(str(self.path))
            raise ctypes.WinError(err)
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            close_handle(self._handle)
            self._handle = None

    def __enter__(self) -> "ExclusiveLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
