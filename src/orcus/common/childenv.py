"""Environment for SDK children (ARCHITECTURE.md §5.4). Never touches system-wide settings.

The only producer of child environments; setup and engine share it.
"""

import importlib.util
import sys
import winreg
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .paths import OrcusPaths

REMOVE_EXACT = frozenset({
    "ANDROID_SERIAL",
    "ADB_SERVER_SOCKET",            # beats the port variables and would bypass the private server
    "ANDROID_ADB_SERVER_ADDRESS",
    "ANDROID_SDK_HOME",
    "ANDROID_PREFS_ROOT",
    "HOME",                         # Git Bash; would redirect .android
    "QML_IMPORT_PATH",
    "QML2_IMPORT_PATH",
})
REMOVE_PREFIXES = ("ANDROID_EMULATOR_", "QEMU_", "QT_")
ADB_LOCAL_TRANSPORT_MAX_PORT = 5553   # turns off the 5555.. startup scan (~2 s per closed port here)


def app_dirs_to_strip() -> list[Path]:
    """Folders our own process may have put on PATH: PyInstaller dir, PySide6, the venv."""
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    spec = importlib.util.find_spec("PySide6")
    if spec is not None and spec.submodule_search_locations:
        dirs.extend(Path(p) for p in spec.submodule_search_locations)
    if sys.prefix != sys.base_prefix:
        dirs.append(Path(sys.prefix))
    return dirs


def _norm(p: str | Path) -> str:
    return str(p).rstrip("\\/").lower()


def clean_path(path_value: str, strip_dirs: Iterable[Path]) -> str:
    roots = [_norm(d) for d in strip_dirs]
    kept: list[str] = []
    for entry in path_value.split(";"):
        e = _norm(entry)
        if not e:
            continue
        if any(e == r or e.startswith(r + "\\") for r in roots):
            continue
        kept.append(entry)
    return ";".join(kept)


def base_env(paths: OrcusPaths, source: Mapping[str, str], *, adb_server_port: int,
             strip_dirs: Iterable[Path] = ()) -> dict[str, str]:
    """Environment for the emulator, emulator-check and one-shot adb clients."""
    env = {k.upper(): v for k, v in source.items()}   # Windows names are case-insensitive
    for key in list(env):
        if key in REMOVE_EXACT or key.startswith(REMOVE_PREFIXES):
            del env[key]
    env["PATH"] = clean_path(env.get("PATH", ""), strip_dirs)
    sdk = str(paths.sdk)
    home = str(paths.android_home)
    env.update({
        "ANDROID_HOME": sdk,
        "ANDROID_SDK_ROOT": sdk,
        "ANDROID_AVD_HOME": str(paths.avd_dir),
        "ANDROID_EMULATOR_HOME": home,
        "ANDROID_USER_HOME": home,
        "TMP": str(paths.tmp),
        "TEMP": str(paths.tmp),
        "ANDROID_ADB_SERVER_PORT": str(adb_server_port),   # the emulator registers itself here
    })
    return env


def adb_server_env(paths: OrcusPaths, source: Mapping[str, str], *, adb_server_port: int,
                   strip_dirs: Iterable[Path] = ()) -> dict[str, str]:
    """The private adb server: no USB phones, no port scan, the shared key. ADB_EMU stays unset."""
    env = base_env(paths, source, adb_server_port=adb_server_port, strip_dirs=strip_dirs)
    env.update({
        "ADB_USB": "0",
        "ADB_LOCAL_TRANSPORT_MAX_PORT": str(ADB_LOCAL_TRANSPORT_MAX_PORT),
        "ADB_VENDOR_KEYS": str(paths.adbkey),
    })
    return env


@dataclass(frozen=True)
class IeProxy:
    enabled: bool
    server: str | None
    autoconfig_url: str | None


@dataclass(frozen=True)
class ProxyDecision:
    http_proxy_flag: str | None   # value for -http-proxy, or None
    source: str                   # "env" | "system" | "none"
    warning_key: str | None       # e.g. "proxy.pac_unsupported"


INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def read_ie_proxy() -> IeProxy:
    def value(key: winreg.HKEYType, name: str) -> object:
        try:
            return winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, INTERNET_SETTINGS) as key:
            enabled = value(key, "ProxyEnable")
            server = value(key, "ProxyServer")
            pac = value(key, "AutoConfigURL")
    except OSError:
        return IeProxy(False, None, None)
    return IeProxy(enabled == 1, server if isinstance(server, str) and server else None,
                   pac if isinstance(pac, str) and pac else None)


def _pick_http(server: str) -> str:
    if "=" not in server:
        value = server
    else:
        entries = dict(part.split("=", 1) for part in server.split(";") if "=" in part)
        value = entries.get("http") or entries.get("https") or next(iter(entries.values()))
    return value if "://" in value else f"http://{value}"


def decide_proxy(env: Mapping[str, str], read_ie: Callable[[], IeProxy] = read_ie_proxy) -> ProxyDecision:
    """http_proxy/https_proxy pass through (the guest uses them). Otherwise forward an enabled
    Windows system proxy with -http-proxy; the emulator does not read it. PAC is not supported."""
    upper = {k.upper() for k in env}
    if "HTTP_PROXY" in upper or "HTTPS_PROXY" in upper:
        return ProxyDecision(None, "env", None)
    ie = read_ie()
    warning = "proxy.pac_unsupported" if ie.autoconfig_url else None
    if ie.enabled and ie.server:
        return ProxyDecision(_pick_http(ie.server), "system", warning)
    return ProxyDecision(None, "none", warning)
