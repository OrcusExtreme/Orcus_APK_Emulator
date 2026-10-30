"""S4 fix candidate (throwaway): never save the golden in the provisioning session.

A golden saved right after the -change-locale framework restart wakes up with a stale clock and freezes
~21 s after every load; snapshots saved in a normal cold session load fine even minutes later.
1. PROVISION boot (-change-locale ko-KR): wait for the framework restart, apply settings (read back), stop.
2. Clean COLD boot (no -change-locale; settings persist in userdata): settle, verify, RAM-only props and
   markers, save orcus_golden.
3. Wait, then GOLDEN boot: clock right after load, and adb + frames + keys for 90 s.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_provision_v2.py [wait_before_load_s]
"""

import json
import sys
import threading
import time
import uuid

from harness import Live, LaunchMode, say
from provision_draft import PROVISION, RAM_PROPS, apply_checked, sh, wait_framework_restart

from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

WAIT_S = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
GOLDEN = "orcus_golden"
report: dict[str, object] = {"wait_before_load_s": WAIT_S}
provision_id = uuid.uuid4().hex[:12]

with Live(mode=LaunchMode.PROVISION) as live:
    report["provision_boot"] = dict(live.timings)
    report["provision_ready_s"] = wait_framework_restart(live)
    report["provision"] = apply_checked(live, PROVISION)

with Live(mode=LaunchMode.COLD) as live:
    report["clean_boot"] = dict(live.timings)
    time.sleep(10)   # settle
    report["persisted"] = {check: sh(live, check)[1][:30] for _, check, _ in PROVISION}
    report["locale"] = sh(live, "getprop persist.sys.locale")[1]
    report["ram_props"] = apply_checked(live, RAM_PROPS)
    sh(live, "setprop debug.orcus.session ''")
    sh(live, f"setprop debug.orcus.golden {provision_id}")
    live.snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=60)
    saved = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=300)
    report["saved"] = {"success": saved.success, "err": saved.err.decode(errors="replace")}

say(f"golden saved; waiting {WAIT_S:.0f} s before loading it")
time.sleep(WAIT_S)

with Live(mode=LaunchMode.GOLDEN) as live:
    t0 = time.monotonic()
    report["golden_boot"] = dict(live.timings)
    out = sh(live, "date +%s")[1]
    report["skew_after_load"] = round(int(out) - time.time(), 1) if out.isdigit() else out
    report["markers"] = [sh(live, "getprop debug.orcus.golden")[1], sh(live, "getprop debug.orcus.session")[1]]
    frames: list[float] = []
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=216, height=384), timeout=120)

    def pump() -> None:
        try:
            for _ in stream:
                frames.append(round(time.monotonic() - t0, 1))
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()
    first_bad, next_key = None, 10.0
    while time.monotonic() - t0 < 90:
        now = time.monotonic() - t0
        if now >= next_key:
            next_key += 10
            live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
                eventType=ec.KeyboardEvent.keypress, key="AppSwitch" if int(now) // 10 % 2 else "GoHome"))]), timeout=5)
        if live.adb("shell", "echo ok", timeout_s=3).out.strip() != "ok" and first_bad is None:
            first_bad = round(now, 1)
        time.sleep(1)
    stream.cancel()
    report["first_adb_failure_s"] = first_bad
    report["frames_after_25s"] = len([t for t in frames if t > 25])
    report["frames_after_60s"] = len([t for t in frames if t > 60])
    report["su_after_90s"] = sh(live, "su 0 id")[1][:30]
report["provision_id"] = provision_id
print(json.dumps(report, ensure_ascii=False, indent=2))
