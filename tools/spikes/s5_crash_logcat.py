"""S5 follow-up (throwaway): how to crash an app on purpose, and which logcat path delivers the lines.

`am crash <pkg>` left the Companion's pid unchanged, and gRPC streamLogcat in Parsed mode sent nothing.
Here: am crash output, crash buffer, gRPC streamLogcat in Text mode (lines are in `contents`).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s5_crash_logcat.py
"""

import json
import threading
import time
from pathlib import Path

from harness import Live, LaunchMode

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = Path(r"D:\OrcusDev\fixtures\MITAI2Companion.apk")
PKG = "edu.mit.appinventor.aicompanion3"
report: dict[str, object] = {}

with Live(mode=LaunchMode.GOLDEN) as live:
    live.adb("install", "-r", "-g", str(APK), timeout_s=180)
    live.shell("am", "start", "-W", "-n", f"{PKG}/.Screen1", timeout_s=60)
    time.sleep(2)
    lines: list[str] = []
    stream = live.stub.streamLogcat(ec.LogMessage(), timeout=40)   # Text mode

    def pump() -> None:
        try:
            for msg in stream:
                lines.extend(msg.contents.splitlines())
                lines.extend(f"[entry] {e.tag}: {e.msg}" for e in msg.entries)
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()
    time.sleep(3)
    backlog = len(lines)
    pid = live.shell("pidof", PKG).out.strip()
    crash = live.shell("am", "crash", PKG)
    time.sleep(4)
    report["am_crash"] = {"code": crash.code, "out": (crash.out + crash.err).strip()[:200], "pid_before": pid,
                          "pid_after": live.shell("pidof", PKG).out.strip()}
    if report["am_crash"]["pid_after"] == pid:   # try by pid
        crash2 = live.shell("am", "crash", pid)
        time.sleep(4)
        report["am_crash_by_pid"] = {"code": crash2.code, "out": (crash2.out + crash2.err).strip()[:200],
                                     "pid_after": live.shell("pidof", PKG).out.strip()}
    report["crash_buffer"] = live.adb("logcat", "-d", "-b", "crash", "-v", "threadtime", timeout_s=20).out.strip().splitlines()[:25]
    stream.cancel()
    new = lines[backlog:]
    report["grpc_text_logcat"] = {"backlog_lines": backlog, "new_lines": len(new),
                                  "first_backlog_line": lines[0][:160] if lines else None,
                                  "crash_lines": [line[:200] for line in new if "FATAL" in line or "AndroidRuntime" in line
                                                  or " E " in line and PKG in line][:15]}
print(json.dumps(report, ensure_ascii=False, indent=2))
