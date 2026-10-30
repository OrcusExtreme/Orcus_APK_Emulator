"""S4 diagnosis 2 (throwaway): is the adb death ~25 s after a golden load caused by the clock jump?

GOLDEN boot -> within 2 s set the guest clock with root `date` -> watch adb `echo` for 60 s. In parallel:
is the guest UI alive (a Home key at +30 s and +45 s produces new frames?) and does the logcat stream
deliver anything (count every entry, keep warnings and errors)?
Pass `--no-fix` to skip the clock fix (control run).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_adb_after_load2.py [--no-fix]
"""

import json
import sys
import threading
import time

from harness import Live, LaunchMode

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

FIX = "--no-fix" not in sys.argv
report: dict[str, object] = {"clock_fix": FIX}
log = {"entries": 0, "contents_chars": 0, "warn": []}
frames = {"n": 0, "times": []}

with Live(mode=LaunchMode.GOLDEN) as live:
    t0 = time.monotonic()
    report["boot"] = dict(live.timings)
    r = live.adb("shell", "date +%s", timeout_s=5)
    report["skew_before"] = round(int(r.out.strip()) - time.time(), 1) if r.out.strip().isdigit() else r.out
    if FIX:
        stamp = time.strftime("%m%d%H%M%Y.%S", time.gmtime(time.time() + 1))
        fix = live.adb("shell", f"su 0 date -u {stamp}", timeout_s=10)
        r = live.adb("shell", "date +%s", timeout_s=5)
        report["fix"] = {"code": fix.code, "timed_out": fix.timed_out, "out": (fix.out + fix.err).strip()[:80],
                         "skew_after": round(int(r.out.strip()) - time.time(), 1) if r.out.strip().isdigit() else r.out}

    logs = live.stub.streamLogcat(ec.LogMessage(), timeout=90)

    def pump_log() -> None:
        try:
            for msg in logs:
                log["entries"] += len(msg.entries)
                log["contents_chars"] += len(msg.contents)
                for e in msg.entries:
                    if e.level >= ec.LogcatEntry.WARN:
                        log["warn"].append(f"+{time.monotonic() - t0:5.1f} {e.tag}: {e.msg[:120]}")
                for line in msg.contents.splitlines():
                    if " E " in line or " W " in line or " F " in line:
                        log["warn"].append(f"+{time.monotonic() - t0:5.1f} {line[:150]}")
        except Exception as exc:
            log["warn"].append(f"stream end {type(exc).__name__}")
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=216, height=384), timeout=90)

    def pump_frames() -> None:
        try:
            for _ in stream:
                frames["n"] += 1
                frames["times"].append(round(time.monotonic() - t0, 1))
        except Exception:
            pass
    threading.Thread(target=pump_log, daemon=True).start()
    threading.Thread(target=pump_frames, daemon=True).start()

    samples: list[str] = []
    first_bad = None
    keys_sent = set()
    while time.monotonic() - t0 < 60:
        now = time.monotonic() - t0
        for at in (30, 45):
            if now >= at and at not in keys_sent:
                keys_sent.add(at)
                live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
                    eventType=ec.KeyboardEvent.keypress, key="GoHome"))]), timeout=5)
        echo = live.adb("shell", "echo ok", timeout_s=3)
        ok = echo.out.strip() == "ok"
        if not ok and first_bad is None:
            first_bad = round(now, 1)
        samples.append(f"+{now:4.1f} {'ok' if ok else 'TIMEOUT' if echo.timed_out else 'fail'}")
        time.sleep(0.5)
    report["first_bad_s"] = first_bad
    report["samples"] = samples[::8]
    report["frames"] = {"n": frames["n"], "after_30s": [t for t in frames["times"] if t >= 30][:6]}
    report["logcat"] = {"entries": log["entries"], "contents_chars": log["contents_chars"], "warn": log["warn"][-25:]}
    logs.cancel()
    stream.cancel()
print(json.dumps(report, ensure_ascii=False, indent=2))
