"""S4 clock spike (throwaway): after a golden load the guest clock is the golden's save time. Which fix works?

Each attempt is timed, its timeout is reported, and the shell is checked for life afterwards.
A: network time (Android's own way): `cmd network_time_update_service force_refresh`
B: root date: `su 0 date -u MMDDhhmmYYYY.ss`
C: `cmd alarm set-time <ms>` (hung the shell in the previous run; last, with a short timeout)

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_clock.py
"""

import json
import time

from harness import Live, LaunchMode

report: dict[str, object] = {}


def run(live: Live, cmd: str, timeout_s: float = 15) -> dict[str, object]:
    t = time.monotonic()
    r = live.adb("shell", cmd, timeout_s=timeout_s)
    return {"cmd": cmd, "code": r.code, "timed_out": r.timed_out, "s": round(time.monotonic() - t, 2),
            "out": (r.out + r.err).strip()[:160]}


def skew(live: Live) -> float | None:
    r = live.adb("shell", "date +%s", timeout_s=10)
    text = r.out.strip()
    return round(int(text) - time.time(), 1) if text.isdigit() else None


def alive(live: Live) -> bool:
    return live.adb("shell", "echo ok", timeout_s=10).out.strip() == "ok"


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["skew_after_load"] = skew(live)
    report["auto_time"] = run(live, "settings get global auto_time")
    report["network"] = run(live, "dumpsys connectivity | grep -m3 -E 'NetworkAgentInfo|VALIDATED'")

    a = run(live, "cmd network_time_update_service force_refresh", timeout_s=30)
    time.sleep(2)
    report["A_network_time"] = {**a, "skew_after": skew(live), "alive": alive(live)}

    host = time.gmtime(time.time() + 1)
    stamp = time.strftime("%m%d%H%M%Y.%S", host)
    b = run(live, f"su 0 date -u {stamp}")
    report["B_su_date"] = {**b, "skew_after": skew(live), "alive": alive(live)}

    c = run(live, f"cmd alarm set-time {int(time.time() * 1000)}", timeout_s=10)
    report["C_alarm_set_time"] = {**c, "skew_after": skew(live), "alive": alive(live)}
print(json.dumps(report, ensure_ascii=False, indent=2))
