"""S4 diagnosis (throwaway): does adb die some seconds after a golden load, and why?

GOLDEN boot, then for 60 s every 0.5 s: adb get-state, `echo`, gRPC getStatus (uptime). Meanwhile
gRPC streamLogcat (independent of adb) keeps the guest log lines that look relevant. At the end: try the
re-registration message and `adb reconnect`, and check the shell again.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_adb_after_load.py
"""

import json
import re
import threading
import time

from google.protobuf import empty_pb2
from harness import Live, LaunchMode, register_emulator

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

KEYWORDS = re.compile(r"adbd|watchdog|Watchdog|FATAL|system_server|time|Time|clock|Clock|restart|ANR|died|kill",
                      re.IGNORECASE)
report: dict[str, object] = {}
log_lines: list[str] = []

with Live(mode=LaunchMode.GOLDEN) as live:
    t0 = time.monotonic()
    report["boot"] = dict(live.timings)
    stream = live.stub.streamLogcat(ec.LogMessage(sort=ec.LogMessage.Parsed), timeout=90)

    def pump() -> None:
        try:
            for msg in stream:
                for e in msg.entries:
                    line = f"+{time.monotonic() - t0:5.1f} {ec.LogcatEntry.LogLevel.Name(e.level)[0]} {e.tag}: {e.msg[:150]}"
                    if KEYWORDS.search(e.tag + " " + e.msg) and e.level >= ec.LogcatEntry.INFO:
                        log_lines.append(line)
        except Exception as exc:
            log_lines.append(f"logcat stream ended: {type(exc).__name__}")
    threading.Thread(target=pump, daemon=True).start()

    samples: list[str] = []
    first_bad = None
    while time.monotonic() - t0 < 60:
        st = live.adb("get-state", timeout_s=3).out.strip() or "?"
        echo = live.adb("shell", "echo ok", timeout_s=3)
        ok = echo.out.strip() == "ok"
        try:
            up = live.stub.getStatus(empty_pb2.Empty(), timeout=3).uptime
        except Exception:
            up = -1
        samples.append(f"+{time.monotonic() - t0:4.1f}s state={st} echo={'ok' if ok else ('TIMEOUT' if echo.timed_out else 'fail')} uptime={up}")
        if not ok and first_bad is None:
            first_bad = round(time.monotonic() - t0, 1)
        time.sleep(0.5)
    report["first_bad_s"] = first_bad
    report["samples_head"] = samples[:6]
    report["samples_tail"] = samples[-4:]
    report["devices"] = live.adb("devices", timeout_s=5).out.strip().splitlines()
    report["reregister"] = register_emulator(live.ports.adb_server, live.ports.adb)
    time.sleep(1)
    report["echo_after_reregister"] = live.adb("shell", "echo ok", timeout_s=5).out.strip()
    report["reconnect"] = live.adb("reconnect", timeout_s=10).out.strip()
    time.sleep(3)
    report["echo_after_reconnect"] = live.adb("shell", "echo ok", timeout_s=5).out.strip()
    report["guest_log"] = log_lines[:40]
    stream.cancel()
print(json.dumps(report, ensure_ascii=False, indent=2))
