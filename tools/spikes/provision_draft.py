"""Provisioning script draft (Phase 2 deliverable 8; becomes engine/provision.py in Phase 3a). No side effects on import.

What S4 taught (docs/spikes.md):
- `-change-locale` restarts the framework ~5 s after boot completes; wait for that restart and for the
  services to answer before running any setting.
- Never save the golden in that provisioning session: such a golden wakes up with a stale clock and the
  guest freezes ~21 s after every load. Stop, cold boot again (settings persist in userdata), save there.
- RAM-only properties (debug.*) and the markers must be set in the session that saves the golden.
"""

import time

from harness import Live

SERVICES = ("settings", "lock_settings", "location", "package", "activity", "input_method")

# (command, read-back command, expected substring of the read-back). All persist in userdata.
PROVISION = [
    ("settings put system accelerometer_rotation 1", "settings get system accelerometer_rotation", "1"),
    ("settings put system screen_off_timeout 2147483647", "settings get system screen_off_timeout", "2147483647"),
    ("svc power stayon true", "dumpsys power | grep -m1 mStayOn=", "mStayOn=true"),
    ("locksettings set-disabled true", "locksettings get-disabled", "true"),
    ("cmd location set-location-enabled true", "cmd location is-location-enabled", "true"),
    ("settings put global verifier_verify_adb_installs 0", "settings get global verifier_verify_adb_installs", "0"),
    ("settings put global package_verifier_enable 0", "settings get global package_verifier_enable", "0"),
    # hw.mainKeys=yes does not hide navigation on API 36 (gesture nav, navigation_mode=2); 3-button nav removes
    # the edge gestures that could steal Canvas drags (S7)
    ("cmd overlay enable-exclusive --category com.android.internal.systemui.navbar.threebutton",
     "settings get secure navigation_mode", "0"),
    # the virtio touch devices report TOUCHSCREEN | STYLUS, so Gboard opens its full-screen "try your stylus" sheet
    # on the first text-field tap of every session; it blocks taps and paste (fx_stylus3)
    ("settings put secure stylus_handwriting_enabled 0", "settings get secure stylus_handwriting_enabled", "0"),
    # the netsim Bluetooth stack crashes right after every golden load (S5); off before the save, it cannot
    ("cmd bluetooth_manager disable", "settings get global bluetooth_on", "0"),
    # Korean input is a paste; with this on, every paste shows "<app> pasted from your clipboard" (S8, fx_s7b)
    ("settings put secure clipboard_show_access_notifications 0",
     "settings get secure clipboard_show_access_notifications", "0"),
]
# SettingsProvider writes its XML up to ~2 s after a change, and the harness's SHUTDOWN is a power-off: the last
# setting of a session was lost twice (bluetooth_on, stylus_handwriting_enabled). flush() before ending a session.
# RAM only: set in the saving session (and again in session_init for COLD sessions)
RAM_PROPS = [("setprop debug.wm.disable_deprecated_target_sdk_dialog true",
              "getprop debug.wm.disable_deprecated_target_sdk_dialog", "true")]


def sh(live: Live, cmd: str, timeout_s: float = 30) -> tuple[int, str]:
    r = live.adb("shell", cmd, timeout_s=timeout_s)
    return r.code, (r.out + r.err).strip()


def services_up(live: Live) -> bool:
    code, out = sh(live, "; ".join(f"service check {s}" for s in SERVICES))
    return code == 0 and out.count(": found") == len(SERVICES)


def wait_framework_restart(live: Live, timeout_s: float = 120) -> float:
    """After a -change-locale boot: wait for start_count to change, then 5 healthy checks in a row."""
    t0 = time.monotonic()
    first = sh(live, "getprop sys.system_server.start_count")[1]
    while time.monotonic() - t0 < timeout_s:
        if sh(live, "getprop sys.system_server.start_count")[1] != first and services_up(live):
            break
        time.sleep(1)
    while not all(services_up(live) for _ in range(5)) and time.monotonic() - t0 < timeout_s:
        time.sleep(1)
    return round(time.monotonic() - t0, 1)


def flush(live: Live, settle_s: float = 3.0) -> str:
    """Let SettingsProvider write its delayed XML, then sync the filesystem, before the session is powered off."""
    time.sleep(settle_s)
    return sh(live, "sync; echo synced")[1]


def apply_checked(live: Live, steps: list[tuple[str, str, str]], timeout_s: float = 30) -> dict[str, str]:
    """Run each command until its read-back shows the wanted value (or give up after timeout_s)."""
    out: dict[str, str] = {}
    for cmd, check, want in steps:
        deadline = time.monotonic() + timeout_s
        while True:
            code, text = sh(live, cmd)
            _, back = sh(live, check)
            if code == 0 and want in back:
                out[cmd] = "ok"
                break
            if time.monotonic() > deadline:
                out[cmd] = f"FAILED code={code} {text[:80]} / {back[:60]}"
                break
            time.sleep(1)
    return out
