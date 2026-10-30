"""Fixture spike, step 0 (throwaway): do the basics work with the real web-IDE builds?

Install the three APKs, launch OrcusProbe, dump the UI (does it ever go idle with sensor labels updating?),
tap the canvas with a gRPC MouseEvent and read the label back, take screenshots.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_explore.py
"""

import json
import sys
import time

from fx_common import FIXTURES, Live, dump, install, launch, package_of, resumed, say, screenshot, tap

from harness import LaunchMode

report: dict[str, object] = {}
apks = {name: FIXTURES / f"{name}.apk" for name in ("HelloPurr", "TalkToMe", "OrcusProbe")}
pkgs = {name: package_of(apk) for name, apk in apks.items()}

with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["screen_size"] = live.shell("wm", "size").out.strip() + " / " + live.shell("wm", "density").out.strip()
    for name, apk in apks.items():
        report[f"install_{name}"] = install(live, apk, grant=True)
    probe = pkgs["OrcusProbe"]
    report["launch_probe"] = launch(live, probe)
    time.sleep(2)
    report["resumed"] = resumed(live)
    screenshot(live, "probe_start")
    try:
        d = dump(live, quiet_pkg=probe)
        report["dump"] = {"attempts": d.attempts, "seconds": d.seconds, "rotation": d.rotation,
                          "texts": d.texts()[:40]}
        canvas = next((n for n in d.nodes if n.pkg == probe and n.cls == "android.view.View" and n.size[1] > 300), None)
        report["canvas"] = canvas.bounds if canvas else None
        if canvas:
            x1, y1, x2, y2 = canvas.bounds
            targets = [(x1 + (x2 - x1) // 4, y1 + (y2 - y1) // 4), (x1 + 3 * (x2 - x1) // 4, y1 + 3 * (y2 - y1) // 4)]
            taps = []
            for tx, ty in targets:
                tap(live, tx, ty)
                time.sleep(0.8)
                d2 = dump(live, quiet_pkg=probe)
                taps.append({"panel": (tx, ty), "label": d2.label("touch"), "dump_attempts": d2.attempts,
                             "dump_s": d2.seconds})
            report["taps"] = taps
    except Exception as e:
        report["dump_error"] = f"{type(e).__name__}: {e}"
    screenshot(live, "probe_after_taps")
    report["window_focus"] = live.adb("shell", "dumpsys window | grep -m1 mCurrentFocus").out.strip()

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
