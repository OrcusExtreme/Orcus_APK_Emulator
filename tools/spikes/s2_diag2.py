"""S2 diagnosis 2 (throwaway): where does 37.2.12 put the frame size? Attach only, no launch."""

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from orcus.common import winjob  # noqa: E402
from orcus.emugrpc import channel as grpc_channel  # noqa: E402
from orcus.emugrpc.generated import emulator_controller_pb2 as ec  # noqa: E402
from orcus.engine import discovery  # noqa: E402

rec = discovery.find(discovery.candidate_dirs(os.environ), serial_port=5640,
                     avd_dir=Path(r"D:\OrcusAPK\avd\orcus36.avd"), alive=winjob.pid_alive)
assert rec is not None and rec.grpc_port and rec.grpc_token
ch = grpc_channel.make_channel(rec.grpc_port, rec.grpc_token)
stub = grpc_channel.controller(ch)
seen: list[dict[str, object]] = []
stream = stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=432, height=768))


def pump() -> None:
    try:
        for img in stream:
            seen.append({"image.width": img.width, "image.height": img.height, "bytes": len(img.image),
                         "format.width": img.format.width, "format.height": img.format.height,
                         "format.format": ec.ImageFormat.ImgFormat.Name(img.format.format),
                         "rotation": ec.Rotation.SkinRotation.Name(img.format.rotation.rotation),
                         "seq": img.seq, "ts": img.timestampUs})
            if len(seen) >= 2:
                return
    except Exception as e:
        seen.append({"error": repr(e)})


t = threading.Thread(target=pump, daemon=True)
t.start()
t.join(10)
stream.cancel()
png = stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.PNG), timeout=15)
print(json.dumps({"stream": seen, "png_format": [png.format.width, png.format.height],
                  "png_magic": png.image[:8].hex()}, indent=2))
ch.close()
