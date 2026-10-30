"""Frame stream (ARCHITECTURE.md §9.1): streamScreenshot RGB888 on the `frames` thread.

37.2.12 puts the size in Image.format and leaves Image.width/height at 0 (S2); only frames whose length
does not match format.width*height*3 are dropped. The stream sends the current screen at once and then
only when the screen changes. The rotation of recent frames is kept so input can map a click on frame
`seq` with that frame's rotation (§9.2).
"""

import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import grpc

from orcus.common import log, messages_ko
from orcus.contracts import ErrorCode, Frame, OrcusError, Rotation
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

_log = log.get("device.frames")
MAX_SIDE = (720, 1280)
_SEQ_MEMORY = 128


def to_frame(img: ec.Image) -> Frame | None:
    w, h = img.format.width, img.format.height
    if not w or not h or len(img.image) != w * h * 3:
        return None
    return Frame(rgb888=img.image, width=w, height=h, rotation=Rotation(int(img.format.rotation.rotation)),
                 seq=img.seq, ts_us=img.timestampUs)


class FrameStream:
    """FrameSource over one gRPC stub. start/retarget/stop may be called from any thread."""

    def __init__(self, stub: Any) -> None:
        self._stub = stub
        self._lock = threading.Lock()
        self._call: Any = None
        self._thread: threading.Thread | None = None
        self._gen = 0
        self._rotations: "OrderedDict[int, Rotation]" = OrderedDict()
        self.latest_rotation = Rotation.PORTRAIT
        self._on_frame: Callable[[Frame], None] | None = None
        self._on_end: Callable[[OrcusError | None], None] | None = None
        self._size = MAX_SIDE

    def start(self, width: int, height: int, on_frame: Callable[[Frame], None],
              on_end: Callable[[OrcusError | None], None]) -> None:
        with self._lock:
            self._on_frame, self._on_end = on_frame, on_end
        self.retarget(width, height)

    def retarget(self, width: int, height: int) -> None:
        """Cancel the current stream and request again at the new size (never above 720x1280)."""
        width, height = max(1, int(width)), max(1, int(height))
        scale = min(1.0, MAX_SIDE[0] / min(width, height), MAX_SIDE[1] / max(width, height))
        with self._lock:
            self._size = (round(width * scale), round(height * scale))
            self._gen += 1
            gen = self._gen
            old = self._call
            self._call = None
        if old is not None:
            old.cancel()
        t = threading.Thread(target=self._run, args=(gen,), name="frames", daemon=True)
        with self._lock:
            self._thread = t
        t.start()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            old = self._call
            self._call = None
            self._on_frame = None
        if old is not None:
            old.cancel()

    def rotation_of(self, seq: int) -> Rotation:
        with self._lock:
            return self._rotations.get(seq, self.latest_rotation)

    def _run(self, gen: int) -> None:
        with self._lock:
            w, h = self._size
        try:
            call = self._stub.streamScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=w, height=h,
                                                              display=0))
        except grpc.RpcError as e:
            self._end(gen, e)
            return
        with self._lock:
            if gen != self._gen:
                call.cancel()
                return
            self._call = call
        error: grpc.RpcError | None = None
        try:
            for img in call:
                frame = to_frame(img)
                if frame is None:
                    continue
                with self._lock:
                    if gen != self._gen:
                        break
                    self._rotations[frame.seq] = frame.rotation
                    while len(self._rotations) > _SEQ_MEMORY:
                        self._rotations.popitem(last=False)
                    self.latest_rotation = frame.rotation
                    cb = self._on_frame
                if cb is not None:
                    cb(frame)
        except grpc.RpcError as e:
            if e.code() != grpc.StatusCode.CANCELLED:
                error = e
        self._end(gen, error)

    def _end(self, gen: int, error: grpc.RpcError | None) -> None:
        with self._lock:
            current = gen == self._gen
            cb = self._on_end
        if not current or cb is None:
            return
        _log.info("frame stream ended: %s", error.code() if error is not None else "normally")
        cb(messages_ko.error(ErrorCode.ENGINE_NO_FRAMES, f"frame stream: {error.code()}") if error is not None
           else None)
