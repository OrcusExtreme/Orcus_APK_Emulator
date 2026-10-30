"""Launch and app-lifecycle shell commands (ARCHITECTURE.md §7.6, §7.9). Owns am/pm/pidof/force-stop.

Pre-rotation: frames follow the device posture, not the guest's display rotation (S7), so a landscape-locked
launcher shows sideways in a portrait posture. The device is turned before `am start`: a "landscape" lock is
drawn at ROTATION_90, which posture z=90 (LANDSCAPE) matches, and "reverseLandscape" at ROTATION_270.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from orcus.contracts import DeviceShell, ErrorCode, Rotation

from .inspect import LANDSCAPE, PORTRAIT

PID_WAIT_S = 5.0
_EXACT = {"landscape": Rotation.LANDSCAPE, "reverseLandscape": Rotation.REVERSE_LANDSCAPE,
          "portrait": Rotation.PORTRAIT, "reversePortrait": Rotation.REVERSE_PORTRAIT}
_DISPLAY = {"0": Rotation.PORTRAIT, "90": Rotation.LANDSCAPE, "180": Rotation.REVERSE_PORTRAIT,
            "270": Rotation.REVERSE_LANDSCAPE}


@dataclass(frozen=True)
class LaunchOutcome:
    ok: bool
    code: ErrorCode | None
    component: str | None
    pid: int | None
    total_ms: int | None
    detail: str


def wanted_rotation(orientation: str | None, current: Rotation) -> Rotation | None:
    """The posture to set before launching, or None to leave it."""
    want = _EXACT.get(orientation or "")
    if want is None and orientation in LANDSCAPE:   # sensorLandscape, userLandscape: either side
        want = current if current in (Rotation.LANDSCAPE, Rotation.REVERSE_LANDSCAPE) else Rotation.LANDSCAPE
    elif want is None and orientation in PORTRAIT:
        want = current if current in (Rotation.PORTRAIT, Rotation.REVERSE_PORTRAIT) else Rotation.PORTRAIT
    return want if want is not None and want != current else None


def display_rotation(shell: DeviceShell) -> Rotation | None:
    """The guest's display rotation (`mCurrentRotation`, 0.06 s in S7); [Match orientation] turns to it."""
    m = re.search(r"mCurrentRotation=ROTATION_(\d+)", shell.shell(["dumpsys", "window", "displays"], 15).out)
    return _DISPLAY.get(m.group(1)) if m else None


def resolve_launcher(shell: DeviceShell, package: str) -> str | None:
    r = shell.shell(["cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER",
                     package], timeout_s=15)
    lines = [line.strip() for line in r.out.splitlines() if "/" in line]
    return lines[-1] if lines else None


def pid_of(shell: DeviceShell, package: str) -> int | None:
    out = shell.shell(["pidof", package], timeout_s=10).out.strip().split()
    return int(out[0]) if out and out[0].isdigit() else None


def force_stop(shell: DeviceShell, package: str) -> None:
    shell.shell(["am", "force-stop", package], timeout_s=15)


def launch(shell: DeviceShell, package: str, launcher: str | None, *,
           sleep: Callable[[float], None] = time.sleep) -> LaunchOutcome:
    component = f"{package}/{launcher}" if launcher else resolve_launcher(shell, package)
    if component is None:
        return LaunchOutcome(False, ErrorCode.APP_LAUNCH_NOT_FOUND, None, None, None, "no launcher activity")
    r = shell.shell(["am", "start", "-W", "-n", component], timeout_s=60)
    if "Status: ok" not in r.out:
        again = resolve_launcher(shell, package)
        if again and again != component:
            component = again
            r = shell.shell(["am", "start", "-W", "-n", component], timeout_s=60)
        if "Status: ok" not in r.out:
            return LaunchOutcome(False, ErrorCode.APP_LAUNCH_NOT_FOUND, component, None, None,
                                 (r.out + r.err).strip()[-300:])
    total = re.search(r"TotalTime: (\d+)", r.out)
    pid = None
    end = time.monotonic() + PID_WAIT_S
    while pid is None:
        pid = pid_of(shell, package)
        if pid is None:
            if time.monotonic() >= end:
                break
            sleep(0.25)
    return LaunchOutcome(True, None, component, pid, int(total.group(1)) if total else None, "")


def crash_buffer(shell: DeviceShell, lines: int = 60) -> str:
    return shell.shell(["logcat", "-d", "-b", "crash", "-v", "threadtime", "-t", str(lines)], timeout_s=15).out


def clear_data(shell: DeviceShell, package: str) -> bool:
    return "Success" in shell.shell(["pm", "clear", package], timeout_s=30).out


def grant_all(shell: DeviceShell, package: str, permissions: frozenset[str]) -> int:
    """`pm grant` each requested permission; ones that cannot be granted are skipped (§7.7)."""
    granted = 0
    for perm in sorted(permissions):
        if shell.shell(["pm", "grant", package, perm], timeout_s=15).code == 0:
            granted += 1
    return granted
