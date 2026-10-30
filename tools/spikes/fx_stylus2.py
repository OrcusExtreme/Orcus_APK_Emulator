"""Fixture spike S8 pre-check 2 (throwaway): replay fx_s5's TalkToMe steps (tap box -> Paste key -> tap button)
with a screenshot after every step, to see which step brings up Gboard's stylus onboarding; and record the raw
evdev tool type (getevent) of a MouseEvent tap versus a TouchEvent tap.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_stylus2.py
"""

import json
import sys
import threading
import time

from fx_common import (FIXTURES, Live, dump, install, launch, package_of, paste, say, screenshot, tap, touch_tap)

from harness import LaunchMode

APK = FIXTURES / "TalkToMe.apk"
PKG = package_of(APK)
report: dict[str, object] = {}


def raw_events(live: Live, action) -> list[str]:   # type: ignore[no-untyped-def]
    """getevent -l for ~3 s around an action; only the tool/btn/pressure lines."""
    box: dict[str, str] = {}

    def run() -> None:
        box["out"] = live.adb("shell", "timeout 3 getevent -l", timeout_s=10).out
    th = threading.Thread(target=run)
    th.start()
    time.sleep(0.8)
    action()
    th.join()
    keep = ("TOOL", "BTN_TOUCH", "PRESSURE", "BTN_STYLUS", "TRACKING_ID")
    return [" ".join(line.split()[1:]) for line in box.get("out", "").splitlines() if any(k in line for k in keep)][:12]


def sheet(live: Live) -> bool:
    return any("스타일러스" in t or "Write here" in t for t in dump(live).texts())


with Live(mode=LaunchMode.GOLDEN) as live:
    report["install"] = install(live, APK, grant=True)
    for how, do_tap in (("mouse", tap), ("touch", touch_tap)):
        say(how)
        live.shell("am", "force-stop", PKG)
        launch(live, PKG)
        time.sleep(1.5)
        d = dump(live)
        box, btn = d.find(cls="android.widget.EditText"), d.find(text="Talk To Me")
        assert box is not None and btn is not None
        steps: dict[str, object] = {}
        steps["raw_tap_box"] = raw_events(live, lambda: do_tap(live, *box.center))
        time.sleep(1.0)
        steps["after_tap_box"] = sheet(live)
        screenshot(live, f"stylus2_{how}_1_box")
        paste(live, "Hello from Orcus")
        time.sleep(1.0)
        steps["after_paste"] = sheet(live)
        screenshot(live, f"stylus2_{how}_2_paste")
        steps["raw_tap_button"] = raw_events(live, lambda: do_tap(live, *btn.center))
        time.sleep(1.0)
        steps["after_tap_button_1s"] = sheet(live)
        screenshot(live, f"stylus2_{how}_3_button")
        time.sleep(5)
        steps["after_tap_button_6s"] = sheet(live)
        screenshot(live, f"stylus2_{how}_4_later")
        steps["spoke"] = "speak called" in live.adb("shell", f"logcat -d -s InternalTTS | tail -3", timeout_s=10).out
        report[how] = steps

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
