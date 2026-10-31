"""Small ctypes wrappers for the Win32 calls several modules share."""

import ctypes
import time
import winreg
from ctypes import wintypes
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STD_INPUT_HANDLE = -10

kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
kernel32.GetSystemDirectoryW.restype = wintypes.UINT
kernel32.GetTickCount64.argtypes = []
kernel32.GetTickCount64.restype = ctypes.c_uint64
kernel32.GetVolumeInformationW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
kernel32.GetVolumeInformationW.restype = wintypes.BOOL
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetConsoleMode.restype = wintypes.BOOL
kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
kernel32.SetDllDirectoryW.restype = wintypes.BOOL
kernel32.IsProcessorFeaturePresent.argtypes = [wintypes.DWORD]
kernel32.IsProcessorFeaturePresent.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
shell32.IsUserAnAdmin.argtypes = []
shell32.IsUserAnAdmin.restype = wintypes.BOOL


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DisplayDevice),
                                       wintypes.DWORD]
user32.EnumDisplayDevicesW.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
MB_ICONERROR = 0x10

PF_VIRT_FIRMWARE_ENABLED = 21


def system_dir() -> Path:
    """C:\\Windows\\System32 from the API, never from environment variables."""
    buf = ctypes.create_unicode_buffer(260)
    n = kernel32.GetSystemDirectoryW(buf, len(buf))
    if n == 0 or n >= len(buf):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buf.value)


def display_device_count() -> int:
    """Display devices this session sees (EnumDisplayDevices). None in a disconnected Remote Desktop session, while
    WMI still lists the GPU; the emulator takes its host GPU from these (3a finding 18)."""
    n = 0
    while True:
        dev = _DisplayDevice()
        dev.cb = ctypes.sizeof(_DisplayDevice)
        if not user32.EnumDisplayDevicesW(None, n, ctypes.byref(dev), 0):
            return n
        n += 1


def message_box(title: str, text: str) -> None:
    """A plain Windows error box, for failures before Qt (the windowed EXE has no console)."""
    user32.MessageBoxW(None, text, title, MB_ICONERROR)


BOOT_ID_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters"


def boot_id() -> int | None:
    """Windows' boot counter (BootId, one more on every boot). Unlike boot_time() it does not move when the
    clock is corrected, so 'same boot' cannot be fooled by a time sync during a long download (3a review)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, BOOT_ID_KEY) as key:
            value, kind = winreg.QueryValueEx(key, "BootId")
    except OSError:
        return None
    return int(value) if kind == winreg.REG_DWORD else None


def boot_time() -> float:
    """Wall-clock time of the last Windows boot (seconds; jitters by a few ms between calls)."""
    return time.time() - kernel32.GetTickCount64() / 1000.0


def volume_info(root: str) -> tuple[str, str] | None:
    """(label, filesystem name) of the volume at `root` such as 'D:\\', or None if it is not there."""
    label = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    ok = kernel32.GetVolumeInformationW(root, label, len(label), None, None, None, fs, len(fs))
    return (label.value, fs.value) if ok else None


def is_user_admin() -> bool:
    """True only when this process already runs elevated."""
    return bool(shell32.IsUserAnAdmin())


advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.DWORD)]
advapi32.GetTokenInformation.restype = wintypes.BOOL
kernel32.GetCurrentProcess.argtypes = []
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
TOKEN_QUERY = 0x0008
TokenElevationType = 18


def elevation_type() -> str:
    """'limited' = admin account behind UAC (consent prompt), 'full' = elevated,
    'default' = standard user or UAC off (the prompt asks for an administrator password)."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return "unknown"
    try:
        value = ctypes.c_int()
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TokenElevationType, ctypes.byref(value),
                                            ctypes.sizeof(value), ctypes.byref(size)):
            return "unknown"
        return {1: "default", 2: "full", 3: "limited"}.get(value.value, "unknown")
    finally:
        close_handle(token.value)


def virtualization_firmware_enabled() -> bool:
    return bool(kernel32.IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED))


def stdin_is_console() -> bool:
    """True only for a real console. Pipes, files and NUL (which isatty() accepts) are rejected."""
    handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return False
    mode = wintypes.DWORD()
    return bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def reset_dll_directory() -> None:
    """Undo SetDllDirectory from the PyInstaller bootloader so children do not inherit it (§5.4)."""
    kernel32.SetDllDirectoryW(None)


def close_handle(handle: int | None) -> None:
    if handle and handle != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(handle)


kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
advapi32.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
kernel32.LocalFree.restype = wintypes.HLOCAL
ERROR_ALREADY_EXISTS = 183
TokenUser = 1


kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenMutexW.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def named_mutex(name: str) -> tuple[int, bool]:
    """(handle, already existed). Keep the handle for the life of the process; the OS frees it at exit."""
    handle = kernel32.CreateMutexW(None, False, name)
    return int(handle or 0), ctypes.get_last_error() == ERROR_ALREADY_EXISTS


def mutex_exists(name: str) -> bool:
    """Whether any process still holds the named mutex (it is gone once the last handle closes)."""
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    close_handle(handle)
    return bool(handle)


def user_sid() -> str:
    """S-1-5-21-... of this process's user (names per-user IPC endpoints, §1.3); '' if unavailable."""
    return _process_sid(kernel32.GetCurrentProcess())


def pipe_server_sid(pipe: int) -> str:
    """User of the process serving a connected named pipe, '' if it cannot be read (another account's process).
    A second start hands its file only to its own account's Orcus: anyone can create a pipe under our name first,
    and the name with our SID in it is no secret (3a review)."""
    pid = wintypes.ULONG()
    if not kernel32.GetNamedPipeServerProcessId(pipe, ctypes.byref(pid)):
        return ""
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        return _process_sid(process)
    finally:
        close_handle(process)


def capture_device_count() -> int:
    """Recording devices Windows offers (waveIn). With none the emulator logs `dsound: Could not initialize
    DirectSoundCapture` and the guest records silence even with the microphone on (S3)."""
    winmm = ctypes.WinDLL("winmm")
    winmm.waveInGetNumDevs.argtypes = []
    winmm.waveInGetNumDevs.restype = wintypes.UINT
    return int(winmm.waveInGetNumDevs())


def _process_sid(process: int) -> str:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return ""
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TokenUser, buf, size, ctypes.byref(size)):
            return ""
        sid_ptr = ctypes.cast(buf, ctypes.POINTER(wintypes.LPVOID))[0]   # TOKEN_USER.User.Sid
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(text)):
            return ""
        try:
            return text.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(text, wintypes.HLOCAL))
    finally:
        close_handle(token.value)
