"""DeviceSession over the emulator's gRPC API (ARCHITECTURE.md §3, §4): one per epoch, own channel.

The engine creates it in SESSION_INIT through the injected factory (`connect`) and closes it on the next
SESSION_INIT, in STOPPING and in FAILED: release_all -> cancel streams -> close the channel.
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import grpc

from orcus.common import log
from orcus.contracts import (
    CancellableIter,
    DeviceControls,
    DeviceEndpoint,
    FrameSource,
    InputSink,
    LogEntry,
    OrcusFailure,
)
from orcus.emugrpc import channel as grpc_channel
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from .controls import ControlQueue, LocationSink
from .frames import FrameStream
from .input import InputQueue, ScrollSink
from .logcat import LogcatStream

_log = log.get("device.session")


type LogcatSource = Callable[[], CancellableIter[LogEntry]]


class GrpcDeviceSession:
    def __init__(self, endpoint: DeviceEndpoint, *, panel: tuple[int, int] = (720, 1280),
                 location: LocationSink | None = None, logcat_source: LogcatSource | None = None,
                 scroll: ScrollSink | None = None, home: tuple[float, float] | None = None,
                 channel_factory: Callable[[int, str], grpc.Channel] = grpc_channel.make_channel) -> None:
        port = int(endpoint.target.rsplit(":", 1)[1])
        self.epoch = endpoint.epoch
        self.serial = endpoint.serial
        self._channel = channel_factory(port, endpoint.token)
        self._stub = grpc_channel.controller(self._channel)
        self._frames = FrameStream(self._stub)
        self._input = InputQueue(self._stub, panel, self._frames.rotation_of, scroll)
        self._controls = ControlQueue(self._stub, location, home)
        self.frames: FrameSource = self._frames
        self.input: InputSink = self._input
        self.controls: DeviceControls | None = self._controls
        self._io = ThreadPoolExecutor(max_workers=1, thread_name_prefix="device-io")
        self._logcat_source = logcat_source
        self._logcats: list[CancellableIter[LogEntry]] = []
        self._closed = False

    def logcat(self) -> CancellableIter[LogEntry]:
        """The injected source (the engine's `adb logcat`, which survives snapshot loads) or gRPC as fallback."""
        stream: CancellableIter[LogEntry] | None = None
        if self._logcat_source is not None:
            try:
                stream = self._logcat_source()
            except (OrcusFailure, OSError) as e:
                _log.warning("logcat source failed, using gRPC: %s", e)
        if stream is None:
            stream = LogcatStream(self._stub)
        self._logcats.append(stream)
        return stream

    def screenshot_png(self, on_done: Callable[[bytes | None], None]) -> None:
        def job() -> None:
            try:
                img = self._stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.PNG), timeout=15)
                on_done(bytes(img.image) if img.image else None)
            except grpc.RpcError as e:
                _log.warning("screenshot failed: %s", e.code())
                on_done(None)
        if not self._closed:
            self._io.submit(job)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._input.close()         # sends the release of a held finger first
        self._frames.stop()
        self._controls.close()
        for s in self._logcats:
            s.cancel()
        self._io.shutdown(wait=False, cancel_futures=True)
        self._channel.close()


def connect(endpoint: DeviceEndpoint, *, panel: tuple[int, int] = (720, 1280),
            location: LocationSink | None = None, logcat_source: LogcatSource | None = None,
            scroll: ScrollSink | None = None, home: tuple[float, float] | None = None) -> GrpcDeviceSession:
    """The DeviceSessionFactory the app gives the engine."""
    return GrpcDeviceSession(endpoint, panel=panel, location=location, logcat_source=logcat_source, scroll=scroll,
                             home=home)
