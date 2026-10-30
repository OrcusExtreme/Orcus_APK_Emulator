"""Enable the Windows Hypervisor Platform with an elevated dism (ARCHITECTURE.md §11.6 step 3).

UAC refusal shows up as ShellExecuteExW returning FALSE with ERROR_CANCELLED (1223), not as a dism
exit code. dism exit codes: 0 / 3010 success (3010 = reboot needed), 3017 and others = failure.
"""

import ctypes
import time
import winreg
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

from orcus.common import win32, winjob
from orcus.common.win32 import close_handle, kernel32
from orcus.contracts import ErrorCode

ERROR_CANCELLED = 1223
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
SW_HIDE = 0
WAIT_OBJECT_0 = 0
DISM_ARGS = "/online /enable-feature /featurename:HypervisorPlatform /all /norestart"
DISM_TIMEOUT_S = 30 * 60
RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_NAME = "OrcusAPKSetup"


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


win32.shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
win32.shell32.ShellExecuteExW.restype = wintypes.BOOL


@dataclass(frozen=True)
class Launch:
    ok: bool
    last_error: int
    process: int | None   # handle, owned by the caller


@dataclass(frozen=True)
class DismOutcome:
    launched: bool
    last_error: int | None
    exit_code: int | None
    in_probe_job: bool | None   # S0a: does the elevated child join the caller's job?
    seconds: float


def dism_path() -> str:
    return str(win32.system_dir() / "dism.exe")   # from the API, no %SystemRoot% expansion


def shell_execute_runas(file: str, params: str) -> Launch:
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = file
    info.lpParameters = params
    info.nShow = SW_HIDE
    ok = bool(win32.shell32.ShellExecuteExW(ctypes.byref(info)))
    err = ctypes.get_last_error() if not ok else 0
    return Launch(ok, err, info.hProcess if ok and info.hProcess else None)


def _wait_exit_code(handle: int, timeout_s: float) -> int | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if kernel32.WaitForSingleObject(handle, 1000) == WAIT_OBJECT_0:
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return int(code.value)
            return None
    return None


def run_dism(*, probe_job: bool = True,
             launch: Callable[[str, str], Launch] = shell_execute_runas) -> DismOutcome:
    """Show UAC, run dism hidden and wait for it. Never kills dism."""
    probe: winjob.Job | None = None
    if probe_job:
        try:   # a job without limits: harmless, only tells us whether elevation inherits it
            probe = winjob.Job.create(kill_on_close=False)
            probe.assign_current()
        except OSError:
            probe = None
    start = time.monotonic()
    result = launch(dism_path(), DISM_ARGS)
    if not result.ok or result.process is None:
        return DismOutcome(False, result.last_error, None, None, time.monotonic() - start)
    try:
        in_job: bool | None = None
        if probe is not None:
            try:
                in_job = probe.contains(result.process)
            except OSError:
                in_job = None
        code = _wait_exit_code(result.process, DISM_TIMEOUT_S)
        return DismOutcome(True, None, code, in_job, time.monotonic() - start)
    finally:
        close_handle(result.process)


@dataclass(frozen=True)
class DismVerdict:
    ok: bool
    reboot_needed: bool
    error: ErrorCode | None


def classify(outcome: DismOutcome) -> DismVerdict:
    if not outcome.launched:
        code = ErrorCode.SETUP_UAC_DENIED if outcome.last_error == ERROR_CANCELLED else ErrorCode.SETUP_DISM_FAILED
        return DismVerdict(False, False, code)
    if outcome.exit_code == 0:
        return DismVerdict(True, False, None)
    if outcome.exit_code == 3010:
        return DismVerdict(True, True, None)
    return DismVerdict(False, False, ErrorCode.SETUP_DISM_FAILED)   # 3017, timeouts and the rest


def register_runonce(command: str, name: str = RUNONCE_NAME) -> None:
    """Resume setup after the reboot. Windows deletes the value before running it."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def unregister_runonce(name: str = RUNONCE_NAME) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUNONCE_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
