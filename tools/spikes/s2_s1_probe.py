"""S2 + S1 spike (throwaway): frames, renderer, input latency; TTS / speech / IME inventory.

S2: guest GLES renderer per GPU mode; streamScreenshot fps, bytes/s and our CPU at 432x768 and 720x1280
in RGB888 and RGBA8888 while the screen scrolls; row order against a PNG screenshot; time from a touch
to the next frame (idle screen). S1: what the google_apis image ships for TTS, speech and IMEs.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s2_s1_probe.py [host|host_hidden|software]
"""

import json
import statistics
import sys
import threading
import time

from harness import Live, LaunchMode, say

from orcus.contracts import GpuMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

GPU = GpuMode(sys.argv[1]) if len(sys.argv) > 1 else GpuMode.HOST
report: dict[str, object] = {"gpu": str(GPU)}


def shell(live: Live, cmd: str, timeout_s: float = 30) -> str:
    r = live.adb("shell", cmd, timeout_s=timeout_s)
    return (r.out + r.err).strip()


def s1(live: Live) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, cmd in {
        "build": "getprop ro.build.fingerprint; getprop ro.build.type; getprop ro.debuggable",
        "locale": "getprop persist.sys.locale; getprop ro.product.locale; getprop persist.sys.timezone",
        "tts_services": "cmd package query-services -a android.intent.action.TTS_SERVICE | grep -E 'packageName|name='",
        "tts_default": "settings get secure tts_default_synth; settings get secure tts_default_locale",
        "speech_activities": "cmd package query-activities -a android.speech.action.RECOGNIZE_SPEECH | grep -E 'packageName|name='",
        "speech_services": "cmd package query-services -a android.speech.RecognitionService | grep -E 'packageName|name='",
        "imes": "ime list -s",
        "packages": "pm list packages | grep -i -E 'tts|speech|voice|google|inputmethod|latin|velvet|gms' | sort",
        "google_tts_version": "dumpsys package com.google.android.tts | grep -m2 -E 'versionName|versionCode'",
    }.items():
        out[key] = shell(live, cmd).splitlines()
    return out


def renderer(live: Live) -> list[str]:
    lines = shell(live, "dumpsys SurfaceFlinger | grep -i -E 'GLES|EGL' | head -5").splitlines()
    return [line.strip()[:200] for line in lines]


def drag(live: Live, stop: threading.Event) -> None:
    """Keep the Settings list scrolling with mouse drags over the input stream."""
    def events():
        while not stop.is_set():
            for y in range(1000, 399, -60):
                yield ec.InputEvent(mouse_event=ec.MouseEvent(x=360, y=y, buttons=1))
                time.sleep(0.012)
            yield ec.InputEvent(mouse_event=ec.MouseEvent(x=360, y=400, buttons=0))
            time.sleep(0.25)
            for y in range(400, 1001, 60):
                yield ec.InputEvent(mouse_event=ec.MouseEvent(x=360, y=y, buttons=1))
                time.sleep(0.012)
            yield ec.InputEvent(mouse_event=ec.MouseEvent(x=360, y=1000, buttons=0))
            time.sleep(0.25)
    live.stub.streamInputEvent(events())


def measure(live: Live, fmt: int, width: int, height: int, seconds: float = 6.0) -> dict[str, object]:
    frames: list[tuple[float, int, int]] = []
    stop = threading.Event()
    mover = threading.Thread(target=drag, args=(live, stop), daemon=True)
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=fmt, width=width, height=height))
    cpu0, t0 = time.process_time(), time.monotonic()
    mover.start()
    try:
        for img in stream:
            frames.append((time.time(), img.timestampUs, len(img.image)))
            if time.monotonic() - t0 >= seconds:
                break
    finally:
        stream.cancel()
        stop.set()
        mover.join(5)
    wall = time.monotonic() - t0
    ages = [now - ts / 1e6 for now, ts, _ in frames if ts > 1e15]   # timestampUs looks like wall-clock µs?
    return {"fmt": ec.ImageFormat.ImgFormat.Name(fmt), "size": f"{width}x{height}", "frames": len(frames),
            "fps": round(len(frames) / wall, 1), "MBps": round(sum(b for *_, b in frames) / wall / 1e6, 1),
            "our_cpu_pct": round(100 * (time.process_time() - cpu0) / wall, 1),
            "bytes_per_frame": frames[-1][2] if frames else 0,
            "first_ts_us": frames[0][1] if frames else None,
            "median_age_ms": round(1000 * statistics.median(ages), 1) if ages else None}


def screenshot(live: Live, fmt: int, tries: int = 20) -> ec.Image:
    """getScreenshot without a size returned 0x0 here; ask for the panel size and skip empty images."""
    for _ in range(tries):
        img = live.stub.getScreenshot(ec.ImageFormat(format=fmt, width=720, height=1280), timeout=10)
        if img.format.width and img.format.height and img.image:   # Image.width/height are 0 in 37.2.12
            return img
        time.sleep(0.25)
    raise RuntimeError("only empty screenshots")


def row_order(live: Live) -> dict[str, object]:
    from PySide6.QtGui import QImage
    empty = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888), timeout=10)
    raw = screenshot(live, ec.ImageFormat.RGB888)
    png = screenshot(live, ec.ImageFormat.PNG)
    img = QImage.fromData(png.image, "PNG").convertToFormat(QImage.Format.Format_RGB888)
    report["screenshot_without_size"] = f"{empty.format.width}x{empty.format.height} bytes={len(empty.image)}"
    w, h = raw.format.width, raw.format.height
    same = flipped = 0
    for y in range(0, h, h // 16):
        for x in range(0, w, w // 8):
            px = raw.image[(y * w + x) * 3:(y * w + x) * 3 + 3]
            top = img.pixelColor(x, y)
            bottom = img.pixelColor(x, h - 1 - y)
            same += (abs(px[0] - top.red()) + abs(px[1] - top.green()) + abs(px[2] - top.blue())) < 12
            flipped += (abs(px[0] - bottom.red()) + abs(px[1] - bottom.green()) + abs(px[2] - bottom.blue())) < 12
    return {"raw": f"{w}x{h}", "png": f"{img.width()}x{img.height()}", "match_top_down": same, "match_flipped": flipped,
            "rotation": ec.Rotation.SkinRotation.Name(raw.format.rotation.rotation)}


def touch_latency(live: Live, trials: int = 10) -> dict[str, object]:
    """Idle screen, press a Settings row: ms until the next frame arrives (the ripple)."""
    results: list[float] = []
    latest = {"t": 0.0}
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=432, height=768))

    def pump() -> None:
        try:
            for _ in stream:
                latest["t"] = time.monotonic()
        except Exception:
            pass
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        for i in range(trials):
            quiet_since = time.monotonic()
            while time.monotonic() - max(latest["t"], quiet_since) < 1.0:   # 1 s without frames
                time.sleep(0.05)
            y = 300 + (i % 5) * 120
            sent = time.monotonic()
            live.stub.sendMouse(ec.MouseEvent(x=360, y=y, buttons=1), timeout=5)
            while latest["t"] < sent and time.monotonic() - sent < 2:
                time.sleep(0.002)
            if latest["t"] >= sent:
                results.append(1000 * (latest["t"] - sent))
            live.stub.sendMouse(ec.MouseEvent(x=360, y=y, buttons=0), timeout=5)
            time.sleep(0.6)
            live.adb("shell", "input keyevent KEYCODE_BACK; am start -W -a android.settings.SETTINGS", timeout_s=20)
    finally:
        stream.cancel()
    return {"trials": trials, "got": len(results),
            "median_ms": round(statistics.median(results), 1) if results else None,
            "max_ms": round(max(results), 1) if results else None, "all_ms": [round(r) for r in results]}


def section(name: str, fn, *a):   # type: ignore[no-untyped-def]
    try:
        report[name] = fn(*a)
    except Exception as e:   # keep the other measurements
        report[name] = f"FAILED {type(e).__name__}: {e}"


def frame_runs(live: Live) -> list[dict[str, object]]:
    runs = []
    for fmt in (ec.ImageFormat.RGB888, ec.ImageFormat.RGBA8888):
        for w, h in ((432, 768), (720, 1280)):
            say("measure", ec.ImageFormat.ImgFormat.Name(fmt), w, h)
            runs.append(measure(live, fmt, w, h))
    return runs


with Live(mode=LaunchMode.COLD, gpu=GPU) as live:
    report["timings"] = live.timings
    section("renderer", renderer, live)
    section("s1", s1, live)
    shell(live, "settings put system screen_off_timeout 2147483647; svc power stayon true; "
                "am start -W -a android.settings.SETTINGS")
    time.sleep(2)
    section("row_order", row_order, live)
    section("frames", frame_runs, live)
    section("touch_latency", touch_latency, live)

print(json.dumps(report, ensure_ascii=False, indent=2))
