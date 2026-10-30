"""S4 freeze hunt (throwaway): load the real golden with -show-kernel and read what the guest kernel says.

The freeze hits about 21 s after the load, which is the default RCU CPU stall timeout. With -show-kernel
the guest console goes into the emulator log, so stall / soft lockup / hung task reports become visible.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_kernel_log.py [extra emulator args...]
"""

import json
import re
import sys
import time

from harness import Live, LaunchMode

EXTRA = sys.argv[1:]
PATTERN = re.compile(r"rcu|stall|lockup|hung_task|blocked for more than|watchdog|BUG|Call Trace|clocksource|"
                     r"tsc|hrtimer|timekeeping|NMI|vcpu|CPU\d+", re.IGNORECASE)
report: dict[str, object] = {"extra": EXTRA}

with Live(mode=LaunchMode.GOLDEN, argv_transform=lambda argv: [*argv, "-show-kernel", *EXTRA]) as live:
    t0 = time.monotonic()
    report["boot"] = dict(live.timings)
    first_bad = None
    while time.monotonic() - t0 < 45:
        ok = live.adb("shell", "echo ok", timeout_s=3).out.strip() == "ok"
        if not ok and first_bad is None:
            first_bad = round(time.monotonic() - t0, 1)
        time.sleep(1)
    report["first_adb_failure_s"] = first_bad
    lines = live.emulator_log.read_text(encoding="utf-8", errors="replace").splitlines()
    loaded = next((i for i, line in enumerate(lines) if "Successfully loaded snapshot" in line), 0)
    report["kernel_after_load"] = [line[:200] for line in lines[loaded:] if PATTERN.search(line)][:40]
    report["log_lines_after_load"] = len(lines) - loaded
print(json.dumps(report, ensure_ascii=False, indent=2))
