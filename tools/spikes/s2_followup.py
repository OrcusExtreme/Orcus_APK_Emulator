"""S2 follow-up (throwaway): getScreenshot behaviour, row order against the guest's own screencap,
the GLES renderer string, and drag-to-frame latency (no tap-timeout in the way).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s2_followup.py [host|host_hidden|software]
"""

import json
import statistics
import subprocess
import sys
import threading
import time

from harness import Live, LaunchMode
from PySide6.QtGui import QImage

from orcus.common import proc
from orcus.contracts import GpuMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

GPU = GpuMode(sys.argv[1]) if len(sys.argv) > 1 else GpuMode.HOST
report: dict[str, object] = {"gpu": str(GPU)}


def section(name: str, fn, *a):   # type: ignore[no-untyped-def]
    try:
        report[name] = fn(*a)
    except Exception as e:
        report[name] = f"FAILED {type(e).__name__}: {e}"


def get_screenshot_matrix(live: Live) -> list[str]:
    out = []
    for fmt in (ec.ImageFormat.PNG, ec.ImageFormat.RGB888, ec.ImageFormat.RGBA8888):
        for w, h in ((0, 0), (720, 1280), (432, 768)):
            img = live.stub.getScreenshot(ec.ImageFormat(format=fmt, width=w, height=h), timeout=15)
            out.append(f"{ec.ImageFormat.ImgFormat.Name(fmt)} req={w}x{h} -> format {img.format.width}x"
                       f"{img.format.height} (image.width {img.width}) {len(img.image)}B seq={img.seq}")
    return out


def first_frame(live: Live, w: int, h: int) -> ec.Image:
    """The size is in Image.format (Image.width/height are 0 in 37.2.12); the stream has a deadline."""
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=w, height=h), timeout=20)
    try:
        for img in stream:
            if len(img.image) == img.format.width * img.format.height * 3 > 0:
                return img
    finally:
        stream.cancel()
    raise RuntimeError("no frame")


def row_order(live: Live) -> dict[str, object]:
    frame = first_frame(live, 720, 1280)
    png = subprocess.run([str(live.paths.adb_exe), "-H", "127.0.0.1", "-P", str(live.ports.adb_server), "-s",
                          live.serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=30,
                         creationflags=proc.CREATE_NO_WINDOW).stdout
    ref = QImage.fromData(png, "PNG").convertToFormat(QImage.Format.Format_RGB888)
    w, h = frame.format.width, frame.format.height
    same = flipped = total = 0
    for y in range(8, h, h // 20):
        for x in range(8, w, w // 10):
            i = (y * w + x) * 3
            px = frame.image[i:i + 3]
            a, b = ref.pixelColor(x, y), ref.pixelColor(x, h - 1 - y)
            total += 1
            same += sum(abs(p - q) for p, q in zip(px, (a.red(), a.green(), a.blue()))) < 24
            flipped += sum(abs(p - q) for p, q in zip(px, (b.red(), b.green(), b.blue()))) < 24
    return {"frame": f"{w}x{h}", "screencap": f"{ref.width()}x{ref.height()}", "samples": total,
            "match_top_to_bottom": same, "match_flipped": flipped}


def renderer(live: Live) -> list[str]:
    lines = live.adb("shell", "dumpsys SurfaceFlinger | grep -E '^GLES:|GLES: '").out.strip().splitlines()
    log = live.emulator_log.read_text(encoding="utf-8", errors="replace").splitlines()
    host = [line.strip()[:160] for line in log if "Renderer" in line or "renderer" in line or "GL_RENDERER" in line]
    return [line.strip()[:200] for line in lines] + host[:6]


def drag_latency(live: Live, trials: int = 8) -> dict[str, object]:
    """Home screen idle; press, then move 200 px: ms from the move until the next frame."""
    live.adb("shell", "input keyevent KEYCODE_HOME")
    time.sleep(2)
    latest = {"t": 0.0}
    stream = live.stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=432, height=768))

    def pump() -> None:
        try:
            for _ in stream:
                latest["t"] = time.monotonic()
        except Exception:
            pass
    threading.Thread(target=pump, daemon=True).start()
    results = []
    try:
        for _ in range(trials):
            quiet = time.monotonic()
            while time.monotonic() - max(latest["t"], quiet) < 1.0 and time.monotonic() - quiet < 10:
                time.sleep(0.02)
            live.stub.sendMouse(ec.MouseEvent(x=360, y=900, buttons=1), timeout=5)
            time.sleep(0.03)
            sent = time.monotonic()
            live.stub.sendMouse(ec.MouseEvent(x=360, y=700, buttons=1), timeout=5)   # past touch slop: drag
            while latest["t"] < sent and time.monotonic() - sent < 2:
                time.sleep(0.001)
            if latest["t"] >= sent:
                results.append(1000 * (latest["t"] - sent))
            live.stub.sendMouse(ec.MouseEvent(x=360, y=700, buttons=0), timeout=5)
            time.sleep(0.8)
            live.adb("shell", "input keyevent KEYCODE_HOME")
    finally:
        stream.cancel()
    return {"got": len(results), "median_ms": round(statistics.median(results), 1) if results else None,
            "all_ms": [round(r) for r in results]}


with Live(mode=LaunchMode.COLD, gpu=GPU) as live:
    report["timings"] = live.timings
    live.adb("shell", "settings put system screen_off_timeout 2147483647; svc power stayon true")
    section("renderer", renderer, live)
    section("get_screenshot", get_screenshot_matrix, live)
    section("row_order", row_order, live)
    section("drag_latency", drag_latency, live)
print(json.dumps(report, ensure_ascii=False, indent=2))
