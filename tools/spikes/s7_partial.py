"""S7 partial spike (throwaway), without OrcusProbe: nav bar, bezel keys, rotation.

- hw.mainKeys=yes: is the guest navigation bar gone? (window list, navigation_mode)
- GoBack / GoHome / AppSwitch keypresses: does the focused window change as expected?
- Rotate the virtual device (setPhysicalModel ROTATION z=90): display rotation, frame format.rotation/size.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s7_partial.py
"""

import json
import re
import time

from harness import Live, LaunchMode

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

report: dict[str, object] = {}


def focus(live: Live) -> str:
    out = live.adb("shell", "dumpsys window | grep -m1 mCurrentFocus", timeout_s=10).out.strip()
    m = re.search(r"\{[^ ]+ [^ ]+ ([^}]+)\}", out)
    return m.group(1) if m else out[:80]


def key(live: Live, name: str) -> None:
    live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
        eventType=ec.KeyboardEvent.keypress, key=name))]), timeout=5)
    time.sleep(1.5)


def frame_info(live: Live) -> str:
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=432, height=768), timeout=10)
    try:
        for img in stream:
            return (f"{img.format.width}x{img.format.height} rotation="
                    f"{ec.Rotation.SkinRotation.Name(img.format.rotation.rotation)}")
    finally:
        stream.cancel()
    return "no frame"


def rotation(live: Live) -> str:
    out = live.adb("shell", "dumpsys window displays | grep -m2 -E 'mRotation|mCurrentRotation|rotation='",
                   timeout_s=10).out
    return " ".join(out.split())[:120]


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["navigation_mode"] = live.adb("shell", "settings get secure navigation_mode").out.strip()
    report["nav_bar_windows"] = live.adb("shell", "dumpsys window windows | grep -c -i NavigationBar").out.strip()
    report["config_show_nav"] = live.adb("shell", "cmd overlay list | grep -i -m3 navbar").out.strip().splitlines()

    live.adb("shell", "am start -W -a android.settings.SETTINGS")
    keys = {"settings_open": focus(live)}
    key(live, "GoBack")
    keys["after_GoBack"] = focus(live)
    key(live, "AppSwitch")
    keys["after_AppSwitch"] = focus(live)
    key(live, "GoHome")
    keys["after_GoHome"] = focus(live)
    report["keys"] = keys

    report["rotation_before"] = {"display": rotation(live), "frame": frame_info(live)}
    live.stub.setPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION,
                                                     value=ec.ParameterValue(data=[0.0, 0.0, 90.0])), timeout=5)
    time.sleep(3)
    report["rotation_z90"] = {"display": rotation(live), "frame": frame_info(live),
                              "user_rotation": live.adb("shell", "settings get system user_rotation").out.strip()}
    live.stub.setPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION,
                                                     value=ec.ParameterValue(data=[0.0, 0.0, 0.0])), timeout=5)
    time.sleep(3)
    report["rotation_back"] = {"display": rotation(live), "frame": frame_info(live)}
print(json.dumps(report, ensure_ascii=False, indent=2))
