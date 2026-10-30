"""Small ctypes wrappers for the Win32 calls several modules share."""

import ctypes
import time
from ctypes import wintypes
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

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

PF_VIRT_FIRMWARE_ENABLED = 21


def system_dir() -> Path:
    """C:\\Windows\\System32 from the API, never from environment variables."""
    buf = ctypes.create_unicode_buffer(260)
    n = kernel32.GetSystemDirectoryW(buf, len(buf))
    if n == 0 or n >= len(buf):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buf.value)


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
