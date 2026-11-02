"""The back camera shows a picture.

Every launch passes `-camera-back imagefile:<home>\\state\\camera.png`. The camera is part of a snapshot's hardware:
a phone saved with the emulated camera did not load with the flag ("The emulator hardware cannot load snapshot"),
so the flag is on every launch and in the golden's key (a golden made without it is rebuilt once). The emulator
reads the file whenever the camera opens: a new picture shows the next time an app opens the camera, no restart
(the camera app showed the new picture once reopened). Until the user picks one, it shows color bars made here.
"""

import os
import struct
import tempfile
import zlib
from pathlib import Path

from orcus.common.paths import OrcusPaths

BARS = ((235, 235, 235), (235, 235, 16), (16, 235, 235), (16, 235, 16), (235, 16, 235), (235, 16, 16),
        (16, 16, 235))   # the classic test-card colors


def default_png(width: int = 640, height: int = 480) -> bytes:
    """Color bars over a gray ramp, encoded here (nothing third-party ships with Orcus)."""
    bar_w = width // len(BARS)
    top = b"".join(bytes(BARS[min(x // bar_w, len(BARS) - 1)]) for x in range(width))
    ramp = b"".join(bytes((x * 255 // (width - 1),) * 3) for x in range(width))
    rows = [b"\x00" + (top if y < height * 3 // 4 else ramp) for y in range(height)]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))


def put(paths: OrcusPaths, png: bytes | None) -> Path:
    """The camera's picture (PNG); None: the color bars again. Replaced whole, never half-written."""
    target = paths.camera_image
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=".camera-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png if png is not None else default_png())
        os.replace(name, target)
    except OSError:
        Path(name).unlink(missing_ok=True)
        raise
    return target


def ensure(paths: OrcusPaths) -> Path:
    """Before a launch: the emulator needs the file to exist."""
    return paths.camera_image if paths.camera_image.is_file() else put(paths, None)
