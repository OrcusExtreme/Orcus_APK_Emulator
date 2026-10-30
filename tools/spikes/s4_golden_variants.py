"""S4 freeze hunt (throwaway): which snapshot variant keeps the guest alive after a load?

A golden boot froze 20-25 s after the load (no adb shell, no frames, no logcat; host gRPC fine), with
"Failed to create process resource for puid N" in the load log. For each variant: cold boot in that GPU
mode, settle, save a throwaway snapshot by that method, stop; boot it with -force-snapshot-load and
watch adb `echo` + frames (a Home key every 10 s) for 60 s.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_golden_variants.py V1 [V2 ...]
  V1 host / gRPC save / load right away     V2 host_hidden / gRPC save
  V3 host / console `avd snapshot save`     V4 software / gRPC save
"""

import json
import shutil
import sys
import threading
import time
from pathlib import Path

from harness import Live, LaunchMode, say

from orcus.contracts import GpuMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

SNAPS = Path(r"D:\OrcusAPK\avd\orcus36.avd\snapshots")
VARIANTS = {"V1": (GpuMode.HOST, "grpc"), "V2": (GpuMode.HOST_HIDDEN, "grpc"),
            "V3": (GpuMode.HOST, "console"), "V4": (GpuMode.SOFTWARE, "grpc")}


def watch(live: Live, seconds: float = 60) -> dict[str, object]:
    t0 = time.monotonic()
    frames: list[float] = []
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=216, height=384),
                                        timeout=seconds + 20)

    def pump() -> None:
        try:
            for _ in stream:
                frames.append(round(time.monotonic() - t0, 1))
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()
    first_bad = None
    next_key = 10.0
    while time.monotonic() - t0 < seconds:
        now = time.monotonic() - t0
        if now >= next_key:
            next_key += 10
            key = "GoHome" if int(now) // 10 % 2 else "AppSwitch"
            live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
                eventType=ec.KeyboardEvent.keypress, key=key))]), timeout=5)
        ok = live.adb("shell", "echo ok", timeout_s=3).out.strip() == "ok"
        if not ok and first_bad is None:
            first_bad = round(now, 1)
        time.sleep(0.5)
    stream.cancel()
    late = [t for t in frames if t > 25]
    return {"first_adb_failure_s": first_bad, "frames_total": len(frames), "frames_after_25s": len(late),
            "last_frame_s": frames[-1] if frames else None}


def load_log_flags(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"puid_warnings": text.count("Failed to create process resource"),
            "loaded": "Successfully loaded snapshot" in text}


report: dict[str, object] = {}
for name in sys.argv[1:] or ["V1"]:
    gpu, method = VARIANTS[name]
    snap = f"s4v_{name.lower()}"
    shutil.rmtree(SNAPS / snap, ignore_errors=True)
    entry: dict[str, object] = {"gpu": str(gpu), "save": method}
    try:
        with Live(mode=LaunchMode.COLD, gpu=gpu) as live:
            entry["cold_boot"] = dict(live.timings)
            time.sleep(10)   # let the launcher settle
            t = time.monotonic()
            if method == "grpc":
                r = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=snap), timeout=300)
                entry["saved"] = {"success": r.success, "err": r.err.decode(errors="replace")[:80]}
            else:
                r2 = live.adb("emu", "avd", "snapshot", "save", snap, timeout_s=300)
                entry["saved"] = {"code": r2.code, "out": (r2.out + r2.err).strip()[:80]}
            entry["save_s"] = round(time.monotonic() - t, 1)
        say(name, "saved; loading it")
        with Live(mode=LaunchMode.GOLDEN, gpu=gpu, boot_timeout_s=60,
                  argv_transform=lambda argv, s=snap: [s if a == "orcus_golden" else a for a in argv]) as live:
            entry["snapshot_boot"] = dict(live.timings)
            entry["flags"] = load_log_flags(live.emulator_log)
            entry["watch"] = watch(live)
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"
    shutil.rmtree(SNAPS / snap, ignore_errors=True)
    report[name] = entry
    say(name, json.dumps(entry, ensure_ascii=False))
print(json.dumps(report, ensure_ascii=False, indent=2))
