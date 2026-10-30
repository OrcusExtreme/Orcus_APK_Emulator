"""Fixture spike S8 pre-check 3 (throwaway): Gboard's stylus onboarding sheet comes up on the first text-field focus
of every golden session (the virtio touch devices report TOUCHSCREEN | STYLUS). The sheet lives in the IME window,
which uiautomator dumps leave out, so it is detected from screen pixels here.

Session 1: secure stylus_handwriting_enabled=0 before anything -> MouseEvent tap on the TextBox, paste, Talk To Me.
Session 2: no setting, the first tap is a finger TouchEvent.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_stylus3.py
"""

import json
import sys
import time

from fx_common import (FIXTURES, Live, dump, install, launch, package_of, paste, say, screenshot, tap, touch_tap)

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "TalkToMe.apk"
PKG = package_of(APK)
report: dict[str, object] = {}


def sheet_up(live: Live) -> dict[str, object]:
    """The sheet's lavender background fills the middle of the screen; the app (and keyboard) don't."""
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=720, height=1280), timeout=10)
    w = img.format.width

    def px(x: int, y: int) -> tuple[int, int, int]:
        i = (y * w + x) * 3
        return img.image[i], img.image[i + 1], img.image[i + 2]
    samples = [px(360, 330), px(600, 980)]
    lavender = all(225 <= r <= 250 and 225 <= g <= 250 and b >= r + 3 for r, g, b in samples)
    return {"sheet": lavender, "samples": samples}


def run(live: Live, how: str, setting_off: bool) -> dict[str, object]:
    r: dict[str, object] = {}
    if setting_off:
        live.shell("settings", "put", "secure", "stylus_handwriting_enabled", "0")
    r["setting"] = live.shell("settings", "get", "secure", "stylus_handwriting_enabled").out.strip()
    r["install"] = install(live, APK, grant=True)
    launch(live, PKG)
    time.sleep(1.5)
    d = dump(live)
    box, btn = d.find(cls="android.widget.EditText"), d.find(text="Talk To Me")
    assert box is not None and btn is not None
    (tap if how == "mouse" else touch_tap)(live, *box.center)
    time.sleep(2.0)
    r["after_first_tap"] = sheet_up(live)
    screenshot(live, f"stylus3_{how}_{'off' if setting_off else 'default'}")
    paste(live, "Hello from Orcus")
    time.sleep(1.0)
    live.adb("logcat", "-c", timeout_s=10)
    (tap if how == "mouse" else touch_tap)(live, *btn.center)
    time.sleep(4)
    r["spoke"] = "speak called" in live.adb("shell", "logcat -d -s InternalTTS", timeout_s=10).out
    r["field"] = (dump(live).find(cls="android.widget.EditText") or box).text
    return r


for name, how, off in (("mouse_setting_off", "mouse", True), ("touch_first_default", "touch", False)):
    say(name)
    with Live(mode=LaunchMode.GOLDEN) as live:
        report[name] = run(live, how, off)

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
