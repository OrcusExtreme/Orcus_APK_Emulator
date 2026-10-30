from pathlib import Path

from orcus.common import childenv
from orcus.common.childenv import IeProxy
from orcus.common.paths import OrcusPaths

P = OrcusPaths(Path(r"D:\OrcusAPK"))
SOURCE = {
    "Path": r"C:\Windows\system32;D:\OrcusDev\venv\Scripts;D:\OrcusDev\venv\Lib\site-packages\PySide6;C:\Tools",
    "SystemRoot": r"C:\Windows",
    "QT_PLUGIN_PATH": "x", "QT_QPA_PLATFORM": "offscreen", "QML2_IMPORT_PATH": "x", "QML_IMPORT_PATH": "x",
    "ANDROID_EMULATOR_FEATURES": "Vulkan", "ANDROID_EMULATOR_USE_SYSTEM_LIBS": "1", "QEMU_AUDIO_DRV": "none",
    "ANDROID_SERIAL": "emulator-5554", "ADB_SERVER_SOCKET": "tcp:localhost:5037",
    "ANDROID_ADB_SERVER_ADDRESS": "x", "ANDROID_SDK_HOME": "x", "ANDROID_PREFS_ROOT": "x", "HOME": "/c/Users/x",
    "http_proxy": "http://proxy:8080",
}
STRIP = [Path(r"D:\OrcusDev\venv"), Path(r"D:\OrcusDev\venv\Lib\site-packages\PySide6")]


def test_base_env_sets_and_removes() -> None:
    env = childenv.base_env(P, SOURCE, adb_server_port=15037, strip_dirs=STRIP)
    assert env["ANDROID_HOME"] == env["ANDROID_SDK_ROOT"] == r"D:\OrcusAPK\sdk"
    assert env["ANDROID_AVD_HOME"] == r"D:\OrcusAPK\avd"
    assert env["ANDROID_EMULATOR_HOME"] == env["ANDROID_USER_HOME"] == r"D:\OrcusAPK\home"
    assert env["TMP"] == env["TEMP"] == r"D:\OrcusAPK\tmp"
    assert env["ANDROID_ADB_SERVER_PORT"] == "15037"
    for gone in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM", "QML2_IMPORT_PATH", "QML_IMPORT_PATH",
                 "ANDROID_EMULATOR_FEATURES", "ANDROID_EMULATOR_USE_SYSTEM_LIBS", "QEMU_AUDIO_DRV",
                 "ANDROID_SERIAL", "ADB_SERVER_SOCKET", "ANDROID_ADB_SERVER_ADDRESS", "ANDROID_SDK_HOME",
                 "ANDROID_PREFS_ROOT", "HOME"):
        assert gone not in env, gone
    assert not any(k.startswith(("QT_", "QEMU_")) for k in env)
    assert [k for k in env if k.startswith("ANDROID_EMULATOR_")] == ["ANDROID_EMULATOR_HOME"]
    assert env["PATH"] == r"C:\Windows\system32;C:\Tools"
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["HTTP_PROXY"] == "http://proxy:8080"   # kept: the guest uses it
    for key in ("ADB_USB", "ADB_LOCAL_TRANSPORT_MAX_PORT", "ADB_VENDOR_KEYS", "ADB_EMU"):
        assert key not in env


def test_adb_server_env() -> None:
    env = childenv.adb_server_env(P, SOURCE, adb_server_port=15037, strip_dirs=STRIP)
    assert env["ADB_USB"] == "0"
    assert env["ADB_LOCAL_TRANSPORT_MAX_PORT"] == "5553"
    assert env["ADB_VENDOR_KEYS"] == r"D:\OrcusAPK\home\adbkey"
    assert "ADB_EMU" not in env


def test_clean_path_matches_prefix_not_substring() -> None:
    kept = childenv.clean_path(r"D:\OrcusDev\venv2\bin;D:\OrcusDev\venv\Scripts", [Path(r"D:\OrcusDev\venv")])
    assert kept == r"D:\OrcusDev\venv2\bin"


def test_app_dirs_include_this_venv() -> None:
    import sys
    dirs = childenv.app_dirs_to_strip()
    if sys.prefix != sys.base_prefix:
        assert Path(sys.prefix) in dirs


def test_proxy_rules() -> None:
    off = lambda: IeProxy(False, "203.253.185.54:10119", None)  # noqa: E731
    on = lambda: IeProxy(True, "203.253.185.54:10119", None)  # noqa: E731
    split = lambda: IeProxy(True, "ftp=f:21;http=h:80;https=s:443", None)  # noqa: E731
    pac = lambda: IeProxy(False, None, "http://wpad/proxy.pac")  # noqa: E731
    assert childenv.decide_proxy({"HTTPS_PROXY": "x"}, on).source == "env"
    assert childenv.decide_proxy({}, off) == childenv.ProxyDecision(None, "none", None)
    assert childenv.decide_proxy({}, on).http_proxy_flag == "http://203.253.185.54:10119"
    assert childenv.decide_proxy({}, split).http_proxy_flag == "http://h:80"
    assert childenv.decide_proxy({}, pac).warning_key == "proxy.pac_unsupported"


def test_read_ie_proxy_on_this_pc() -> None:
    ie = childenv.read_ie_proxy()
    assert isinstance(ie.enabled, bool)
