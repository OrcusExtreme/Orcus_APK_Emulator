"""S4 freeze hunt (throwaway): is the freeze caused by loading a snapshot minutes after it was saved?

1. Cold boot: does root work for a clock fix? (`su 0 id`, then `adb root` + `id` + setting `date`)
2. Save a throwaway snapshot, stop, wait WAIT_S of host time (guest clock falls behind by that much).
3. Load it and watch adb + frames for 60 s, plus the guest clock and its auto_time setting.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_skew_test.py [wait_seconds]
"""

import json
import shutil
import sys
import threading
import time
from pathlib import Path

from harness import Live, LaunchMode, say

from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

WAIT_S = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
SNAP = "s4_skew"
SNAPS = Path(r"D:\OrcusAPK\avd\orcus36.avd\snapshots")
report: dict[str, object] = {"wait_s": WAIT_S}


def run(live: Live, *argv: str, timeout_s: float = 10) -> dict[str, object]:
    t = time.monotonic()
    r = live.adb(*argv, timeout_s=timeout_s)
    return {"code": r.code, "timed_out": r.timed_out, "s": round(time.monotonic() - t, 1),
            "out": (r.out + r.err).strip()[:100]}


def guest_skew(live: Live) -> float | None:
    out = live.adb("shell", "date +%s", timeout_s=5).out.strip()
    return round(int(out) - time.time(), 1) if out.isdigit() else None


shutil.rmtree(SNAPS / SNAP, ignore_errors=True)
with Live(mode=LaunchMode.COLD) as live:
    report["cold_boot"] = dict(live.timings)
    report["auto_time"] = run(live, "shell", "settings get global auto_time")
    report["su_id"] = run(live, "shell", "su 0 id")
    report["adb_root"] = run(live, "root", timeout_s=20)
    time.sleep(3)
    report["id_after_root"] = run(live, "shell", "id")
    stamp = time.strftime("%m%d%H%M%Y.%S", time.gmtime(time.time() + 1))
    report["root_date_set"] = run(live, "shell", f"date -u {stamp}")
    report["skew_after_root_date"] = guest_skew(live)
    report["adb_unroot"] = run(live, "unroot", timeout_s=20)
    time.sleep(3)
    report["id_after_unroot"] = run(live, "shell", "id")
    time.sleep(5)
    saved = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=SNAP), timeout=300)
    report["saved"] = saved.success

say(f"waiting {WAIT_S:.0f} s before loading the snapshot")
time.sleep(WAIT_S)

with Live(mode=LaunchMode.GOLDEN, boot_timeout_s=60,
          argv_transform=lambda argv: [SNAP if a == "orcus_golden" else a for a in argv]) as live:
    t0 = time.monotonic()
    report["load_boot"] = dict(live.timings)
    report["skew_after_load"] = guest_skew(live)
    frames: list[float] = []
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=216, height=384), timeout=90)

    def pump() -> None:
        try:
            for _ in stream:
                frames.append(round(time.monotonic() - t0, 1))
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()
    first_bad, skews, next_key = None, [], 10.0
    while time.monotonic() - t0 < 60:
        now = time.monotonic() - t0
        if now >= next_key:
            next_key += 10
            live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
                eventType=ec.KeyboardEvent.keypress, key="AppSwitch" if int(now) // 10 % 2 else "GoHome"))]), timeout=5)
        out = live.adb("shell", "date +%s", timeout_s=3).out.strip()
        if out.isdigit():
            skews.append(f"+{now:4.1f}s skew={int(out) - time.time():.0f}")
        elif first_bad is None:
            first_bad = round(now, 1)
        time.sleep(1)
    stream.cancel()
    report["first_adb_failure_s"] = first_bad
    report["skew_samples"] = skews[::5]
    report["frames_after_25s"] = len([t for t in frames if t > 25])
shutil.rmtree(SNAPS / SNAP, ignore_errors=True)
print(json.dumps(report, ensure_ascii=False, indent=2))
