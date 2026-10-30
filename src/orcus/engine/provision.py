"""Device preparation (ARCHITECTURE.md §6.5–§6.6): the only owner of settings/setprop/svc/locksettings commands.

Promoted from the Phase 2 draft (tools/spikes/provision_draft.py) with what the spikes taught:
- `-change-locale` restarts the framework ~5 s after boot: wait for it before any setting (S4).
- SettingsProvider writes its XML up to ~2 s late and SHUTDOWN is a power-off: flush before stopping (Phase 2).
- App Inventor's LocationSensor asks the fused provider at BALANCED power, so gRPC setGps never reaches it;
  locations go through the framework's test providers (S7).
"""

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from orcus.common.clock import Clock
from orcus.contracts import CmdResult, DeviceCaps, GpuMode

type Sh = Callable[[str, float], CmdResult]

SERVICES = ("settings", "lock_settings", "location", "package", "activity", "input_method")

# (command, read-back, wanted substring of the read-back). All persist in userdata.
PROVISION: tuple[tuple[str, str, str], ...] = (
    ("settings put system accelerometer_rotation 1", "settings get system accelerometer_rotation", "1"),
    ("settings put system screen_off_timeout 2147483647", "settings get system screen_off_timeout", "2147483647"),
    ("svc power stayon true", "dumpsys power | grep -m1 mStayOn=", "mStayOn=true"),
    ("locksettings set-disabled true", "locksettings get-disabled", "true"),
    ("cmd location set-location-enabled true", "cmd location is-location-enabled", "true"),
    ("settings put global verifier_verify_adb_installs 0", "settings get global verifier_verify_adb_installs", "0"),
    ("settings put global package_verifier_enable 0", "settings get global package_verifier_enable", "0"),
    # hw.mainKeys=yes does not hide navigation on API 36; 3-button nav keeps edge gestures off the Canvas (S7)
    ("cmd overlay enable-exclusive --category com.android.internal.systemui.navbar.threebutton",
     "settings get secure navigation_mode", "0"),
    # the virtio touch devices report TOUCHSCREEN | STYLUS: Gboard's full-screen stylus sheet blocks input (S8)
    ("settings put secure stylus_handwriting_enabled 0", "settings get secure stylus_handwriting_enabled", "0"),
    # the netsim Bluetooth stack crashes after every golden load unless it is off when the golden is saved (S5)
    ("cmd bluetooth_manager disable", "settings get global bluetooth_on", "0"),
    # Korean input is a paste; this toast would cover the app after every paste (S8)
    ("settings put secure clipboard_show_access_notifications 0",
     "settings get secure clipboard_show_access_notifications", "0"),
)
# RAM only: set in the session that saves the golden, and again in session_init (COLD sessions)
RAM_PROPS: tuple[tuple[str, str, str], ...] = (
    ("setprop debug.wm.disable_deprecated_target_sdk_dialog true",
     "getprop debug.wm.disable_deprecated_target_sdk_dialog", "true"),
)
FLUSH_SETTLE_S = 3.0
CLOCK_SKEW_MAX_S = 5.0
LOCATION_PROVIDERS = ("gps", "fused")


class Cancelled(Exception):
    pass


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def _text(r: CmdResult) -> str:
    return (r.out + r.err).strip()


def services_up(sh: Sh) -> bool:
    r = sh("; ".join(f"service check {s}" for s in SERVICES), 15)
    return r.code == 0 and r.out.count(": found") == len(SERVICES)


def wait_framework_restart(sh: Sh, clock: Clock, cancel: threading.Event | None = None,
                           timeout_s: float = 120) -> float:
    """After a -change-locale boot: wait for sys.system_server.start_count to change (or the time to run
    out), then for five healthy service checks in a row. Returns the seconds waited."""
    t0 = clock.monotonic()
    first = sh("getprop sys.system_server.start_count", 10).out.strip()
    while clock.monotonic() - t0 < timeout_s:
        _check(cancel)
        if sh("getprop sys.system_server.start_count", 10).out.strip() != first and services_up(sh):
            break
        clock.sleep(1)
    healthy = 0
    while healthy < 5 and clock.monotonic() - t0 < timeout_s:
        _check(cancel)
        healthy = healthy + 1 if services_up(sh) else 0
        if healthy < 5:
            clock.sleep(0.5)
    return round(clock.monotonic() - t0, 1)


def apply_checked(sh: Sh, clock: Clock, steps: tuple[tuple[str, str, str], ...],
                  cancel: threading.Event | None = None, timeout_s: float = 30) -> dict[str, str]:
    """Each command until its read-back shows the wanted value; "ok" or "FAILED …" per command."""
    out: dict[str, str] = {}
    for cmd, check, want in steps:
        deadline = clock.monotonic() + timeout_s
        while True:
            _check(cancel)
            r = sh(cmd, 30)
            back = sh(check, 15)
            if r.code == 0 and want in back.out:
                out[cmd] = "ok"
                break
            if clock.monotonic() > deadline:
                out[cmd] = f"FAILED code={r.code} {_text(r)[:80]} / {_text(back)[:60]}"
                break
            clock.sleep(1)
    return out


def flush(sh: Sh, clock: Clock) -> bool:
    """Let SettingsProvider write its delayed XML, then sync, before the session is powered off."""
    clock.sleep(FLUSH_SETTLE_S)
    return "synced" in sh("sync; echo synced", 30).out


def missing(sh: Sh, steps: tuple[tuple[str, str, str], ...] = PROVISION) -> list[str]:
    """Read-backs whose wanted value is absent (after the clean reboot, §6.5 step 7a)."""
    return [check for _, check, want in steps if want not in sh(check, 15).out]


def set_markers(sh: Sh, provision_id: str) -> bool:
    a = sh("setprop debug.orcus.session ''", 10)
    b = sh(f"setprop debug.orcus.golden {provision_id}", 10)
    return a.code == 0 and b.code == 0


def location_setup_commands() -> list[str]:
    cmds = ["appops set com.android.shell android:mock_location allow"]
    for p in LOCATION_PROVIDERS:
        cmds += [f"cmd location providers add-test-provider {p}",
                 f"cmd location providers set-test-provider-enabled {p} true"]
    return cmds


def location_commands(lat: float, lon: float, accuracy_m: float = 5.0) -> list[str]:
    return [f"cmd location providers set-test-provider-location {p} --location {lat:.6f},{lon:.6f} "
            f"--accuracy {accuracy_m:g}" for p in LOCATION_PROVIDERS]


@dataclass(frozen=True)
class SessionInitResult:
    nonce_ok: bool
    location_ok: bool
    clock_skew_s: float | None
    clock_fixed: bool
    failures: tuple[str, ...]


def session_init(sh: Sh, *, nonce: str, lat: float, lon: float, host_time: Callable[[], float] = time.time,
                 rerun_ram_props: bool = True) -> SessionInitResult:
    """§6.6, idempotent, about a second. gRPC parts (microphone) are the manager's."""
    failures: list[str] = []

    def run(cmd: str, timeout_s: float = 15) -> CmdResult:
        r = sh(cmd, timeout_s)
        if r.code != 0:
            failures.append(f"{cmd}: {_text(r)[:80]}")
        return r

    if rerun_ram_props:
        for cmd, _, _ in RAM_PROPS:
            run(cmd)
    run("svc power stayon true")
    nonce_ok = run(f"setprop debug.orcus.session {nonce}").code == 0
    loc = [run(c) for c in [*location_setup_commands(), *location_commands(lat, lon)]]
    skew: float | None = None
    fixed = False
    out = sh("date +%s", 10).out.strip()
    if out.isdigit():
        skew = round(int(out) - host_time(), 1)
        if abs(skew) > CLOCK_SKEW_MAX_S:   # insurance only: clean goldens wake up on time (S4)
            stamp = time.strftime("%m%d%H%M%Y.%S", time.gmtime(host_time()))
            fixed = run(f"su 0 date -u {stamp}").code == 0
    return SessionInitResult(nonce_ok, all(r.code == 0 for r in loc), skew, fixed, tuple(failures))


def _lines(r: CmdResult) -> list[str]:
    return [line.strip() for line in r.out.splitlines() if line.strip()]


def detect_caps(sh: Sh, *, panel: tuple[int, int, int], renderer: str, gpu_mode: GpuMode,
                hypervisor: str) -> DeviceCaps:
    api = sh("getprop ro.build.version.sdk", 10).out.strip()
    tts = re.findall(r"packageName=([\w.]+)",
                     sh("cmd package query-services -a android.intent.action.TTS_SERVICE", 15).out)
    speech = (sh("cmd package query-activities -a android.speech.action.RECOGNIZE_SPEECH", 15).out.count("packageName")
              + sh("cmd package query-services -a android.speech.RecognitionService", 15).out.count("packageName"))
    tts_engine = tts[0] if tts else None
    return DeviceCaps(
        api=int(api) if api.isdigit() else 0,
        abis=tuple(a for a in sh("getprop ro.product.cpu.abilist", 10).out.strip().split(",") if a),
        panel=panel, renderer=renderer, gpu_mode=gpu_mode, hypervisor=hypervisor,
        locale=sh("getprop persist.sys.locale", 10).out.strip(),
        timezone=sh("getprop persist.sys.timezone", 10).out.strip(),
        tts_engine=tts_engine,
        tts_ko=True if tts_engine == "com.google.android.tts" else None,   # S1: Korean voice is built in
        speech_recognizer=speech > 0,
        imes=tuple(_lines(sh("ime list -s", 10))),
    )
