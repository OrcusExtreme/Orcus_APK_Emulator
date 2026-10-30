"""Fixture spike S7 with OrcusProbe (throwaway): rotation and tap coordinates (B5), landscape Screen2, shake with the
three sensitivities (and upside down), tilt vs frame rotation, wheel, drag density, input sticking, location, Back.

Taps are gRPC MouseEvents. For each device posture two hypotheses are tried: the MouseEvent is in the current
display's coordinates ("display"), or in the unrotated panel's (the ARCHITECTURE §9.2 table, "panel").

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s7.py
"""

import json
import re
import sys
import time

from fx_common import (FIXTURES, OUT, Live, accel, drag, dump, install, key, launch, package_of, resumed, rotate, say,
                       screenshot, scroll_to, shake_pulse, tap)

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
W, H, DENSITY = 720, 1280, 2.0
report: dict[str, object] = {}


def section(name: str, fn, *a) -> None:   # type: ignore[no-untyped-def]
    say(name)
    try:
        report[name] = fn(*a)
    except Exception as e:
        report[name] = f"FAILED {type(e).__name__}: {e}"


def label(word: str) -> str | None:
    return dump(live, PROBE).label(word)


def canvas_node(d, min_h: int = 150):   # type: ignore[no-untyped-def]
    return next((n for n in d.nodes if n.pkg == PROBE and n.cls == "android.view.View" and n.size[1] >= min_h), None)


def to_panel(rotation: str, dx: float, dy: float, dw: int, dh: int) -> tuple[int, int]:
    """ARCHITECTURE §9.2 provisional table: frame-normalized (u, v) -> unrotated panel pixels."""
    u, v = dx / dw, dy / dh
    x, y = {"PORTRAIT": (u * W, v * H), "LANDSCAPE": ((1 - v) * W, u * H),
            "REVERSE_PORTRAIT": ((1 - u) * W, (1 - v) * H), "REVERSE_LANDSCAPE": (v * W, (1 - u) * H)}[rotation]
    return round(x), round(y)


def display_rotation_sources() -> dict[str, object]:
    t = time.monotonic()
    surf = live.adb("shell", "dumpsys input | grep -m1 SurfaceOrientation", timeout_s=10).out.strip()
    t1 = time.monotonic()
    win = live.adb("shell", "dumpsys window displays | grep -m1 -o 'mCurrentRotation=[A-Z_0-9]*'", timeout_s=10).out.strip()
    t2 = time.monotonic()
    return {"dumpsys_input": surf, "input_s": round(t1 - t, 2), "dumpsys_window": win, "window_s": round(t2 - t1, 2)}


def tap_test(d, rotation: str, canvas_word: str = "touch") -> list[dict[str, object]]:   # type: ignore[no-untyped-def]
    """Tap two display points inside the visible canvas, once per hypothesis; read the app's canvas coordinates."""
    c = canvas_node(d)
    if c is None:
        return [{"error": "canvas not visible", "texts": d.texts()[:10]}]
    x1, y1, x2, y2 = c.bounds
    dw, dh = (W, H) if d.rotation in (0, 2) else (H, W)
    out = []
    for fx, fy in ((0.25, 0.3), (0.75, 0.7)):
        target = (x1 + fx * (x2 - x1), y1 + fy * (y2 - y1))
        sent: list[tuple[int, int]] = []
        for hyp in ("display", "panel"):
            p = (round(target[0]), round(target[1])) if hyp == "display" else to_panel(rotation, *target, dw, dh)
            if p in sent:
                out.append({"hyp": hyp, "same_point_as": "display"})
                continue
            sent.append(p)
            before = dump(live, PROBE).label(canvas_word)
            tap(live, *p)
            time.sleep(0.6)
            after = dump(live, PROBE).label(canvas_word)
            got = None
            m = re.match(rf"{canvas_word} (-?\d+),(-?\d+)", after or "")
            if m and after != before:
                # the canvas reports density-independent coordinates relative to its own top-left
                got = (round(x1 + int(m.group(1)) * DENSITY), round(y1 + int(m.group(2)) * DENSITY))
            err = None if got is None else (got[0] - round(target[0]), got[1] - round(target[1]))
            out.append({"hyp": hyp, "target_display": (round(target[0]), round(target[1])), "sent": p,
                        "label": after, "landed_display": got, "error_px": err})
    return out


def rotations() -> dict[str, object]:
    out: dict[str, object] = {}
    for z in (0, 90, 180, 270):
        rotate(live, 0, 0, z)
        time.sleep(2.5)
        fr = frame_rotation()
        d = dump(live, PROBE)
        out[f"z{z}"] = {"frame": fr, "uiautomator_rotation": d.rotation, **display_rotation_sources(),
                        "screen_label": d.label("screen"), "canvas": getattr(canvas_node(d), "bounds", None),
                        "taps": tap_test(d, fr)}
        screenshot(live, f"s7_rot{z}")
    rotate(live, 0, 0, 0)
    time.sleep(2)
    return out


def frame_rotation() -> str:
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=144, height=256), timeout=10)
    return ec.Rotation.SkinRotation.Name(img.format.rotation.rotation)


def screen2() -> dict[str, object]:
    r: dict[str, object] = {}
    n = scroll_to(live, "open Screen2", PROBE)
    tap(live, *n.center)
    time.sleep(3)
    r["resumed"] = resumed(live)
    for z in (0, 90):
        rotate(live, 0, 0, z)
        time.sleep(2.5)
        fr = frame_rotation()
        d = dump(live, PROBE)
        r[f"posture_z{z}"] = {"frame": fr, "uiautomator_rotation": d.rotation, **display_rotation_sources(),
                              "texts": d.texts()[:6], "canvas": getattr(canvas_node(d, 100), "bounds", None),
                              "taps": tap_test(d, fr, "touch2")}
        screenshot(live, f"s7_screen2_z{z}")
    rotate(live, 0, 0, 0)
    time.sleep(2)
    d = dump(live, PROBE)
    close = d.find(text="close")
    if close:
        tap(live, *close.center)
        time.sleep(2)
    r["after_close"] = resumed(live)
    if "Screen2" in r["after_close"]:      # the tap missed: Back closes an App Inventor secondary screen
        key(live, "GoBack")
        time.sleep(2)
        r["after_back"] = resumed(live)
    return r


def relaunch() -> None:
    live.shell("am", "force-stop", PROBE)
    launch(live, PROBE)
    time.sleep(2)


def shakes() -> dict[str, object]:
    out: dict[str, object] = {}

    def count() -> int:
        m = re.match(r"shake (\d+)", label("shake") or "")
        return int(m.group(1)) if m else -1

    def trial(name: str, button: str, delta: float) -> None:
        tap(live, *scroll_to(live, button, PROBE).center)
        time.sleep(0.5)
        before = count()
        pulse = shake_pulse(live, delta)
        time.sleep(1.0)
        out[name] = {"sens": label("sens"), "delta": delta, "shakes_added": count() - before, "pulse": pulse}

    relaunch()
    trial("weak_d25", "weak", 25)
    trial("moderate_d16", "moderate", 16)
    trial("strong_d9", "strong", 9)
    trial("moderate_d25", "moderate", 25)
    trial("moderate_d9", "moderate", 9)
    trial("strong_d16", "strong", 16)
    trial("weak_d16", "weak", 16)
    rotate(live, 0, 0, 180)
    time.sleep(2.5)
    trial("upside_down_moderate_d16", "moderate", 16)
    rotate(live, 0, 0, 90)
    time.sleep(2.5)
    trial("landscape_moderate_d16", "moderate", 16)
    rotate(live, 0, 0, 0)
    time.sleep(2)
    sensors = live.adb("shell", "dumpsys sensorservice", timeout_s=20).out
    (OUT / "s7_sensorservice.txt").write_text(sensors, encoding="utf-8")
    out["sensorservice_accel_lines"] = [line.strip()[:160] for line in sensors.splitlines()
                                        if re.search(r"(?i)accelerometer|samplingPeriod|maxDelay", line)][:12]
    return out


def tilts() -> dict[str, object]:
    out: dict[str, object] = {}
    relaunch()
    for z in (0, 90):
        for x, y in ((0, 0), (20, 0), (26, 0), (30, 0), (-20, 0), (0, 20), (0, 30), (0, -20)):
            rotate(live, x, y, z)
            time.sleep(1.5)
            out[f"z{z}_x{x}_y{y}"] = {"frame": frame_rotation(), "accel": accel(live)}
        rotate(live, 0, 0, z)
        time.sleep(1.5)
        d = dump(live, PROBE)
        out[f"z{z}_labels"] = [d.label("accel"), d.label("orient")]
    rotate(live, 0, 0, 0)
    time.sleep(2)
    return out


def wheel() -> dict[str, object]:
    out: dict[str, object] = {}
    relaunch()

    def canvas_top() -> int | None:
        c = canvas_node(dump(live, PROBE), 2)
        return c.bounds[1] if c else None

    for where, (x, y) in (("over_labels", (600, 1050)), ("over_canvas", (360, 400))):
        live.stub.sendMouse(ec.MouseEvent(x=x, y=y, buttons=0), timeout=5)
        top0 = canvas_top()
        live.stub.injectWheel(iter([ec.WheelEvent(dy=-120)] * 3), timeout=5)
        time.sleep(1.0)
        top1 = canvas_top()
        live.stub.injectWheel(iter([ec.WheelEvent(dy=120)] * 3), timeout=5)
        time.sleep(1.0)
        out[where] = {"canvas_top": [top0, top1, canvas_top()], "note": "dy=-120 x3 then +120 x3"}
    return out


def drags() -> dict[str, object]:
    out: dict[str, object] = {}

    def counts() -> tuple[str | None, str | None]:
        d = dump(live, PROBE)
        return d.label("drag"), d.label("fling")

    reset = scroll_to(live, "reset", PROBE)
    for name, steps, step_s in (("moves50_every10ms", 50, 0.010), ("moves25_every20ms", 25, 0.020),
                                ("moves10_every8ms_fast", 10, 0.008), ("moves100_every5ms", 100, 0.005)):
        tap(live, *reset.center)
        time.sleep(0.5)
        t = time.monotonic()
        drag(live, 100, 400, 620, 520, steps, step_s)
        out[name] = {"gesture_s": round(time.monotonic() - t, 2), "labels": counts()}
    # sticking: press and move, never release; then a "tap" elsewhere is only a move of the same pointer
    tap(live, *reset.center)
    time.sleep(0.5)
    live.stub.sendMouse(ec.MouseEvent(x=200, y=400, buttons=1), timeout=5)
    live.stub.sendMouse(ec.MouseEvent(x=260, y=420, buttons=1), timeout=5)
    time.sleep(2.0)
    held = counts()
    live.stub.sendMouse(ec.MouseEvent(x=500, y=600, buttons=1), timeout=5)
    time.sleep(0.5)
    moved = counts()
    touch_before_release = label("touch")
    live.stub.sendMouse(ec.MouseEvent(x=500, y=600, buttons=0), timeout=5)
    time.sleep(0.8)
    out["sticking"] = {"held_2s": held, "second_press_elsewhere": moved, "touch_label_before_release":
                       touch_before_release, "after_release": [*counts(), label("touch")]}
    return out


def location() -> dict[str, object]:
    t = time.monotonic()
    live.stub.setGps(ec.GpsState(latitude=37.5665, longitude=126.9780, altitude=38.0, satellites=8), timeout=5)
    for _ in range(8):
        time.sleep(1.5)
        lbl = label("loc")
        if lbl and lbl != "loc -":
            return {"label": lbl, "seconds": round(time.monotonic() - t, 1)}
    return {"label": label("loc"), "seconds": None}


def back() -> dict[str, object]:
    before = label("back")
    key(live, "GoBack")
    time.sleep(1.0)
    return {"before": before, "after": label("back"), "resumed": resumed(live)}


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["install"] = install(live, APK, grant=True)
    report["launch"] = launch(live, PROBE)
    time.sleep(2)
    section("back", back)
    section("location", location)
    section("drags", drags)
    section("rotations", rotations)
    section("screen2", screen2)
    section("shakes", shakes)
    section("tilts", tilts)
    section("wheel", wheel)

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
