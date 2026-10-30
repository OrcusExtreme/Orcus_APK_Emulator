"""S2 diagnosis (throwaway): attach to the running emulator (no launch) and test the screenshot paths.

Opens a fresh streamScreenshot, changes the screen with adb, counts frames; then asks getScreenshot.
Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s2_diag.py
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from orcus.common import proc, winjob  # noqa: E402
from orcus.emugrpc import channel as grpc_channel  # noqa: E402
from orcus.emugrpc.generated import emulator_controller_pb2 as ec  # noqa: E402
from orcus.engine import discovery  # noqa: E402

ADB = [r"D:\OrcusAPK\sdk\platform-tools\adb.exe", "-H", "127.0.0.1", "-P", "15037", "-s", "emulator-5640"]
rec = discovery.find(discovery.candidate_dirs(os.environ), serial_port=5640,
                     avd_dir=Path(r"D:\OrcusAPK\avd\orcus36.avd"), alive=winjob.pid_alive)
assert rec is not None and rec.grpc_port and rec.grpc_token
ch = grpc_channel.make_channel(rec.grpc_port, rec.grpc_token)
stub = grpc_channel.controller(ch)
out: dict[str, object] = {}


def shot(fmt: int, w: int, h: int) -> str:
    img = stub.getScreenshot(ec.ImageFormat(format=fmt, width=w, height=h), timeout=15)
    return f"{ec.ImageFormat.ImgFormat.Name(fmt)} {w}x{h} -> {img.width}x{img.height} {len(img.image)}B"


def stream_test(w: int, h: int, poke: list[str]) -> dict[str, object]:
    frames: list[tuple[int, int, int]] = []
    stream = stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=w, height=h))

    def pump() -> None:
        try:
            for img in stream:
                frames.append((img.width, img.height, len(img.image)))
        except Exception:
            pass
    t = threading.Thread(target=pump, daemon=True)
    t.start()
    time.sleep(2.0)
    before = len(frames)
    subprocess.run([*ADB, "shell", *poke], capture_output=True, timeout=30, creationflags=proc.CREATE_NO_WINDOW)
    time.sleep(3.0)
    stream.cancel()
    t.join(5)
    return {"req": f"{w}x{h}", "frames_in_first_2s": before, "frames_after_poke": len(frames) - before,
            "sizes": sorted({f"{a}x{b}:{c}" for a, b, c in frames})[:4]}


out["status_booted"] = stub.getStatus(__import__("google.protobuf.empty_pb2", fromlist=["Empty"]).Empty(),
                                      timeout=5).booted
out["stream_432"] = stream_test(432, 768, ["input", "swipe", "360", "1000", "360", "400", "200"])
out["stream_720"] = stream_test(720, 1280, ["input", "swipe", "360", "400", "360", "1000", "200"])
out["getScreenshot"] = [shot(ec.ImageFormat.RGB888, 432, 768), shot(ec.ImageFormat.PNG, 0, 0)]
ch.close()
print(json.dumps(out, indent=2))
