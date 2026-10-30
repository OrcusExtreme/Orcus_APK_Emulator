"""Fixture spike S7/S8 follow-up (throwaway): what fx_s7/fx_s8 could not settle.

1. B5 / ARCHITECTURE §9.2: hold a MouseEvent at a known panel point with `pointer_location` on, find the crosshair
   in the gRPC frame (rotated by device posture), map the frame point back with the §9.2 table, compare.
   Twice: in OrcusProbe (display follows the posture) and on the home screen (display stays portrait).
2. Sideways tilt (rotation about z) near portrait and near landscape: when does the frame flip?
3. Shake with the three sensitivities, logging posture/rotation around each pulse (fx_s7 lost the screen).
4. Location: why setGps did not reach LocationChanged.
5. Clipboard access toast off (`clipboard_show_access_notifications 0`), ASCII key events into a TextBox.
6. Is there any wheel-capable input device in the guest?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s7b.py
"""

import json
import re
import sys
import time

from fx_common import (FIXTURES, OUT, Live, Logcat, accel, dump, install, key, launch, package_of, paste, rotate, say,
                       screenshot, scroll_to, shake_pulse, tap)

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
W, H = 720, 1280
report: dict[str, object] = {}


def section(name: str, fn) -> None:   # type: ignore[no-untyped-def]
    say(name)
    try:
        report[name] = fn()
    except Exception as e:
        report[name] = f"FAILED {type(e).__name__}: {e}"


def frame() -> ec.Image:
    return live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888), timeout=10)


def rotation_name(img: ec.Image) -> str:
    return ec.Rotation.SkinRotation.Name(img.format.rotation.rotation)


def display_rotation() -> str:
    out = live.adb("shell", "dumpsys window displays | grep -m1 -o 'mCurrentRotation=[A-Z_0-9]*'", timeout_s=10).out
    return out.strip().removeprefix("mCurrentRotation=")


def table(rot: str, u: float, v: float) -> tuple[float, float]:
    """ARCHITECTURE §9.2 (provisional): frame-normalized (u, v) -> unrotated panel pixels."""
    return {"PORTRAIT": (u * W, v * H), "LANDSCAPE": ((1 - v) * W, u * H),
            "REVERSE_PORTRAIT": ((1 - u) * W, (1 - v) * H), "REVERSE_LANDSCAPE": (v * W, (1 - u) * H)}[rot]


def crosshair(img: ec.Image) -> tuple[int, int] | None:
    """PointerLocationView draws full-length 1 px lines in (0, 0, 192) through the touched point."""
    w, h, data = img.format.width, img.format.height, img.image

    def blue(i: int) -> bool:
        return data[i] < 50 and data[i + 1] < 50 and 140 <= data[i + 2] <= 240
    rows = [y for y in range(h) if sum(blue((y * w + x) * 3) for x in range(0, w, 4)) > w // 4 * 0.6]
    cols = [x for x in range(w) if sum(blue((y * w + x) * 3) for y in range(0, h, 4)) > h // 4 * 0.6]
    if not rows or not cols:
        return None
    return round(sum(cols) / len(cols)), round(sum(rows) / len(rows))


def probe_point(px: int, py: int) -> dict[str, object]:
    live.stub.sendMouse(ec.MouseEvent(x=px, y=py, buttons=1), timeout=5)
    time.sleep(0.15)
    img = frame()
    live.stub.sendMouse(ec.MouseEvent(x=px, y=py, buttons=0), timeout=5)
    rot = rotation_name(img)
    hit = crosshair(img)
    r: dict[str, object] = {"sent_panel": (px, py), "frame": f"{img.format.width}x{img.format.height} {rot}"}
    if hit is None:
        r["crosshair"] = None
        return r
    u, v = hit[0] / img.format.width, hit[1] / img.format.height
    back = table(rot, u, v)
    r.update({"crosshair_frame_px": hit, "table_panel": (round(back[0]), round(back[1])),
              "error_panel_px": (round(back[0] - px), round(back[1] - py))})
    return r


def mapping() -> dict[str, object]:
    out: dict[str, object] = {}
    live.shell("settings", "put", "system", "pointer_location", "1")
    for context in ("app", "home"):
        if context == "app":
            live.shell("am", "force-stop", PROBE)
            launch(live, PROBE)
            points = ((200, 400), (560, 1000))
        else:
            key(live, "GoHome")
            points = ((200, 400), (560, 700))
        time.sleep(1.5)
        for z in (0, 90, 180, 270):
            rotate(live, 0, 0, z)
            time.sleep(2.5)
            entry: dict[str, object] = {"display": display_rotation()}
            for i, (px, py) in enumerate(points):
                entry[f"p{i}"] = probe_point(px, py)
                time.sleep(0.4)
            out[f"{context}_z{z}"] = entry
        rotate(live, 0, 0, 0)
        time.sleep(2)
    live.shell("settings", "put", "system", "pointer_location", "0")
    return out


def tilt() -> dict[str, object]:
    out: dict[str, object] = {}
    key(live, "GoHome")          # display rotation does not matter for the frame; keep the app out of it
    for base in (0, 90):
        for dz in (0, 10, 20, 24, 26, 28, 30, -10, -20, -24, -26, -28, -30):
            rotate(live, 0, 0, base + dz)
            time.sleep(1.2)
            out[f"z{base}{dz:+d}"] = {"frame": rotation_name(frame()), "accel": accel(live)}
    rotate(live, 0, 0, 0)
    time.sleep(1.5)
    return out


def shakes() -> dict[str, object]:
    out: dict[str, object] = {}
    rotate(live, 0, 0, 0)
    live.shell("am", "force-stop", PROBE)
    launch(live, PROBE)
    time.sleep(2)

    def state() -> dict[str, object]:
        return {"frame": rotation_name(frame()), "display": display_rotation(), "accel": accel(live)}

    def count() -> int:
        m = re.match(r"shake (\d+)", dump(live, PROBE).label("shake") or "")
        return int(m.group(1)) if m else -1

    for name, button, delta in (("weak_d25", "weak", 25), ("moderate_d16", "moderate", 16),
                                ("strong_d9", "strong", 9), ("moderate_d25", "moderate", 25),
                                ("moderate_d9", "moderate", 9), ("strong_d16", "strong", 16),
                                ("weak_d16", "weak", 16)):
        try:
            tap(live, *scroll_to(live, button, PROBE, swipes=2).center)
            time.sleep(0.5)
            before, s0 = count(), state()
            pulse = shake_pulse(live, delta)
            time.sleep(1.0)
            out[name] = {"added": count() - before, "sens": dump(live, PROBE).label("sens"), "before": s0,
                         "after": state(), "restored_rotation": pulse["rotation"]}
        except Exception as e:
            out[name] = f"FAILED {type(e).__name__}: {e}"
            screenshot(live, f"s7b_shake_fail_{name}")
            rotate(live, 0, 0, 0)
            time.sleep(1.5)
    for name, z in (("upside_down_moderate_d16", 180), ("landscape_moderate_d16", 90)):
        rotate(live, 0, 0, z)
        time.sleep(2.5)
        s0 = state()
        live.stub.setPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION,
                                                         value=ec.ParameterValue(data=[0, 0, z]),
                                                         interpolation=ec.PhysicalModelValue.STEP), timeout=5)
        before = count() if z == 180 else None      # landscape: the label is off-screen, use logcat-free count later
        pulse = shake_pulse(live, 16)
        time.sleep(1.0)
        rotate(live, 0, 0, 0)
        time.sleep(2.5)
        after = count()
        out[name] = {"before_state": s0, "pulse": pulse, "count_before": before, "count_after_back_in_portrait": after}
    return out


def location() -> dict[str, object]:
    out: dict[str, object] = {}
    live.shell("am", "force-stop", PROBE)
    launch(live, PROBE)
    time.sleep(2)
    log = Logcat(live, settle_s=1.5)
    out["permissions"] = [line.strip() for line in live.adb(
        "shell", f"dumpsys package {PROBE} | grep -E 'ACCESS_(FINE|COARSE)_LOCATION: granted'", timeout_s=10).out.splitlines()][:4]
    out["providers"] = live.adb("shell", "dumpsys location | grep -m8 -E 'provider|Provider \\['", timeout_s=15).out.strip().splitlines()[:8]
    t = time.monotonic()
    for i, (lat, lon, passive) in enumerate(((37.5665, 126.9780, False), (37.5700, 126.9800, False),
                                             (37.5665, 126.9780, True))):
        live.stub.setGps(ec.GpsState(latitude=lat, longitude=lon, altitude=38.0, satellites=8, passiveUpdate=passive),
                         timeout=5)
        time.sleep(4)
        out[f"after_setGps_{i}"] = {"passive": passive, "label": dump(live, PROBE).label("loc")}
    out["last_locations"] = [line.strip()[:160] for line in live.adb(
        "shell", "dumpsys location | grep -i -m6 -E 'last location|Location\\[gps|Location\\[fused'", timeout_s=15).out.splitlines()]
    out["app_lines"] = [line[19:200] for line in log.since(t, pattern=r"(?i)location")][:10]
    log.close()
    return out


def clipboard_and_keys() -> dict[str, object]:
    out: dict[str, object] = {}
    live.shell("settings", "put", "secure", "clipboard_show_access_notifications", "0")
    live.shell("am", "force-stop", PROBE)
    launch(live, PROBE)
    time.sleep(2)
    log = Logcat(live, settle_s=1.5)
    box = scroll_to(live, "plain", PROBE)
    tap(live, *box.center)
    time.sleep(1.2)
    t = time.monotonic()
    paste(live, "토스트 확인")
    time.sleep(1.0)
    screenshot(live, "s7b_clipboard_toast_off")
    out["toast_log_with_setting_off"] = [line[19:200] for line in log.since(t, pattern=r"ClipboardService")][:4]
    out["pasted"] = [n.text for n in dump(live, PROBE).nodes if n.cls == "android.widget.EditText"][:4]
    multi = scroll_to(live, "multi", PROBE)
    tap(live, *multi.center)
    time.sleep(1.2)
    live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
        eventType=ec.KeyboardEvent.keypress, text="abc"))]), timeout=5)
    time.sleep(1.0)
    out["ascii_key_text_into_multi"] = [n.text for n in dump(live, PROBE).nodes if n.cls == "android.widget.EditText"][:4]
    key(live, "GoBack")
    log.close()
    return out


def wheel_devices() -> dict[str, object]:
    out = live.adb("shell", "getevent -lp 2>/dev/null | grep -E 'add device|name:|REL_WHEEL|REL_HWHEEL'",
                   timeout_s=15).out.splitlines()
    return {"devices_with_wheel": [line.strip() for line in out if "WHEEL" in line][:4],
            "device_names": sorted({line.split('"')[1] for line in out if "name:" in line and '"' in line})}


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["install"] = install(live, APK, grant=True)
    launch(live, PROBE)
    time.sleep(2)
    section("mapping", mapping)
    section("tilt", tilt)
    section("shakes", shakes)
    section("location", location)
    section("clipboard_and_keys", clipboard_and_keys)
    section("wheel_devices", wheel_devices)

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
