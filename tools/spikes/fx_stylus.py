"""Fixture spike S8 pre-check (throwaway): a gRPC MouseEvent tap on a TextBox opened Gboard's
"스타일러스 사용해 보기" (stylus handwriting onboarding) sheet over the app (fx_s5, TalkToMe).

Which input device/tool type does a MouseEvent become, and does a TouchEvent (finger) or the system setting
stylus_handwriting_enabled=0 avoid the sheet?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_stylus.py
"""

import json
import re
import sys
import time

from fx_common import FIXTURES, Live, dump, install, key, launch, package_of, say, screenshot, tap, touch_tap

from harness import LaunchMode

APK = FIXTURES / "TalkToMe.apk"
PKG = package_of(APK)
report: dict[str, object] = {}


def devices(live: Live) -> list[str]:
    out = live.adb("shell", "dumpsys input", timeout_s=20).out
    reader = out.split("Input Reader State")[1] if "Input Reader State" in out else out
    lines = []
    for block in re.split(r"\n\s*Device -?\d+:", reader)[1:]:
        name = block.strip().splitlines()[0].strip()
        sources = re.search(r"Sources: ([^\n]+)", block)
        lines.append(f"{name} | {sources.group(1).strip() if sources else '?'}")
    return lines


def last_motion(live: Live) -> list[str]:
    out = live.adb("shell", "dumpsys input", timeout_s=20).out
    return [m[:220] for m in re.findall(r"MotionEvent\([^\n]*", out)][-3:]


def tap_box(live: Live, how: str) -> dict[str, object]:
    live.shell("am", "force-stop", PKG)
    launch(live, PKG)
    time.sleep(1.5)
    box = dump(live).find(cls="android.widget.EditText")
    assert box is not None
    (tap if how == "mouse" else touch_tap)(live, *box.center)
    time.sleep(2.5)
    d = dump(live)
    screenshot(live, f"stylus_{how}_{report.get('_n', 0)}")
    report["_n"] = int(report.get("_n", 0)) + 1     # type: ignore[arg-type]
    texts = d.texts()
    r = {"stylus_sheet": any("스타일러스" in t or "Write here" in t for t in texts), "texts": texts[:8],
         "ime_shown": "mInputShown=true" in live.adb("shell", "dumpsys input_method | grep -m1 mInputShown",
                                                     timeout_s=10).out,
         "recent_motion": last_motion(live)}
    key(live, "GoBack")
    time.sleep(0.6)
    key(live, "GoBack")
    time.sleep(0.6)
    return r


with Live(mode=LaunchMode.GOLDEN) as live:
    report["input_devices"] = devices(live)
    report["stylus_setting_default"] = live.shell("settings", "get", "secure", "stylus_handwriting_enabled").out.strip()
    report["install"] = install(live, APK, grant=True)
    say("mouse tap")
    report["mouse"] = tap_box(live, "mouse")
    say("mouse tap again (onboarding seen once already)")
    report["mouse_again"] = tap_box(live, "mouse")
    say("finger tap")
    report["touch"] = tap_box(live, "touch")
    live.shell("settings", "put", "secure", "stylus_handwriting_enabled", "0")
    report["stylus_setting_after"] = live.shell("settings", "get", "secure", "stylus_handwriting_enabled").out.strip()
    say("mouse tap with stylus handwriting off")
    report["mouse_setting_off"] = tap_box(live, "mouse")
    report.pop("_n", None)

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
