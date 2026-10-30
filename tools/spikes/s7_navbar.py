"""S7 follow-up (throwaway): force 3-button navigation, and prove that AppSwitch opens Recents.

hw.mainKeys=yes did not hide navigation on API 36 (gesture navigation, navigation_mode=2), and gesture
edges can steal Canvas drags. Try the overlay that switches SystemUI to 3-button navigation.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s7_navbar.py
"""

import hashlib
import json
import time

from harness import Live, LaunchMode

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

THREE = "com.android.internal.systemui.navbar.threebutton"
report: dict[str, object] = {}


def frame_hash(live: Live) -> tuple[str, float]:
    """Hash of one frame plus the brightness of its bottom 6% (where a 3-button bar would be)."""
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=432, height=768), timeout=10)
    w, h = img.format.width, img.format.height
    bottom = img.image[(h - h * 6 // 100) * w * 3:]
    return hashlib.sha1(img.image).hexdigest()[:10], round(sum(bottom) / max(1, len(bottom)), 1)


def key(live: Live, name: str) -> None:
    live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
        eventType=ec.KeyboardEvent.keypress, key=name))]), timeout=5)
    time.sleep(2)


with Live(mode=LaunchMode.GOLDEN) as live:
    home = frame_hash(live)
    key(live, "AppSwitch")
    recents = frame_hash(live)
    key(live, "GoHome")
    report["appswitch_changes_screen"] = {"home": home, "after_AppSwitch": recents, "back_home": frame_hash(live)}

    report["before"] = {"navigation_mode": live.adb("shell", "settings get secure navigation_mode").out.strip(),
                        "bottom_brightness": frame_hash(live)[1]}
    r = live.adb("shell", f"cmd overlay enable-exclusive --category {THREE}", timeout_s=30)
    time.sleep(4)
    report["enable_threebutton"] = (r.code, (r.out + r.err).strip()[:100])
    report["after"] = {"navigation_mode": live.adb("shell", "settings get secure navigation_mode").out.strip(),
                       "overlays": live.adb("shell", "cmd overlay list | grep -i navbar").out.strip().splitlines(),
                       "bottom_brightness": frame_hash(live)[1]}
    live.adb("shell", "am start -W -a android.settings.SETTINGS", timeout_s=30)
    key(live, "GoBack")
    report["back_still_works"] = live.adb("shell", "dumpsys window | grep -m1 mCurrentFocus").out.strip()[-60:]
print(json.dumps(report, ensure_ascii=False, indent=2))
