"""FakeEmulatorServer: an in-process gRPC server built from the generated servicers (ARCHITECTURE.md §12.2).

Shared by the unit tests and `--selftest`. It checks the Bearer token like the real emulator,
synthesizes frames (size in Image.format, Image.width/height 0, like 37.2.12), records input, and lets a
test script snapshot replies (success=false with err). Like the real 37.2.12 (S4), SaveSnapshot of an
existing id overwrites it and DeleteSnapshot always reports success.
"""

import queue
import struct
import threading
import time
import zlib
from collections import deque
from concurrent import futures
from typing import Any

import grpc
from google.protobuf import empty_pb2

from .generated import emulator_controller_pb2 as ec
from .generated import emulator_controller_pb2_grpc as ec_grpc
from .generated import snapshot_service_pb2 as ss
from .generated import snapshot_service_pb2_grpc as ss_grpc

Empty = empty_pb2.Empty


def _deny(request: Any, context: grpc.ServicerContext) -> Any:
    context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing or wrong token")


_HANDLER_FOR = {
    (False, False): grpc.unary_unary_rpc_method_handler,
    (False, True): grpc.unary_stream_rpc_method_handler,
    (True, False): grpc.stream_unary_rpc_method_handler,
    (True, True): grpc.stream_stream_rpc_method_handler,
}


class _TokenCheck(grpc.ServerInterceptor):
    def __init__(self, token: str) -> None:
        self._expected = f"Bearer {token}"

    def intercept_service(self, continuation, handler_call_details):   # type: ignore[no-untyped-def]
        handler = continuation(handler_call_details)
        metadata = dict(handler_call_details.invocation_metadata or ())
        if handler is None or metadata.get("authorization") == self._expected:
            return handler
        make = _HANDLER_FOR[(handler.request_streaming, handler.response_streaming)]   # same shape as the real one
        return make(_deny, request_deserializer=handler.request_deserializer,
                    response_serializer=handler.response_serializer)


def png_bytes(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A solid-color PNG (enough for screenshot tests, no imaging library needed)."""
    row = b"\x00" + bytes(rgb) * width
    raw = zlib.compress(row * height)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


class _Controller(ec_grpc.EmulatorControllerServicer):
    def __init__(self, fake: "FakeEmulatorServer") -> None:
        self.f = fake

    def getStatus(self, request: Empty, context: grpc.ServicerContext) -> ec.EmulatorStatus:
        f = self.f
        f.record("getStatus")
        hw = ec.EntryList(entry=[ec.Entry(key="hw.lcd.width", value=str(f.width)),
                                 ec.Entry(key="hw.lcd.height", value=str(f.height)),
                                 ec.Entry(key="hw.lcd.density", value="320")])
        return ec.EmulatorStatus(version="fake-37.2.12", uptime=int((time.monotonic() - f.started) * 1000),
                                 booted=f.booted, hardwareConfig=hw, vmConfig=ec.VmConfiguration(
                                     hypervisorType=ec.VmConfiguration.WHPX, numberOfCpuCores=4,
                                     ramSizeBytes=2 << 30))

    def getVmState(self, request: Empty, context: grpc.ServicerContext) -> ec.VmRunState:
        self.f.record("getVmState")
        return ec.VmRunState(state=self.f.vm_state)

    def setVmState(self, request: ec.VmRunState, context: grpc.ServicerContext) -> Empty:
        self.f.record("setVmState", request.state)
        self.f.vm_state = request.state
        return Empty()

    def _frame(self, fmt: ec.ImageFormat) -> ec.Image:
        f = self.f
        width = fmt.width or f.width
        height = fmt.height or f.height
        f.seq += 1
        shade = (f.seq * 7) % 256
        if fmt.format == ec.ImageFormat.PNG:
            data = png_bytes(width, height, (shade, 64, 128))
        else:
            data = bytes((shade, 64, 128)) * (width * height)
        out_fmt = ec.ImageFormat(format=fmt.format, width=width, height=height,
                                 rotation=ec.Rotation(rotation=f.rotation))
        # Like the real 37.2.12: the size lives in Image.format; Image.width/height stay 0 (S2).
        return ec.Image(format=out_fmt, image=data, seq=f.seq, timestampUs=int(time.time() * 1e6))

    def getScreenshot(self, request: ec.ImageFormat, context: grpc.ServicerContext) -> ec.Image:
        self.f.record("getScreenshot")
        return self._frame(request)

    def streamScreenshot(self, request: ec.ImageFormat, context: grpc.ServicerContext):   # type: ignore[no-untyped-def]
        self.f.record("streamScreenshot", (request.width, request.height))
        while context.is_active() and not self.f.stopping.is_set():
            yield self._frame(request)
            time.sleep(1 / self.f.fps)

    def streamInputEvent(self, request_iterator, context: grpc.ServicerContext) -> Empty:   # type: ignore[no-untyped-def]
        self.f.record("streamInputEvent")
        for event in request_iterator:
            self.f.input_events.append(event)
        return Empty()

    def sendKey(self, request: ec.KeyboardEvent, context: grpc.ServicerContext) -> Empty:
        self.f.input_events.append(ec.InputEvent(key_event=request))
        return Empty()

    def sendMouse(self, request: ec.MouseEvent, context: grpc.ServicerContext) -> Empty:
        self.f.input_events.append(ec.InputEvent(mouse_event=request))
        return Empty()

    def getSensor(self, request: ec.SensorValue, context: grpc.ServicerContext) -> ec.SensorValue:
        return self.f.sensors.get(request.target, ec.SensorValue(target=request.target))

    def setSensor(self, request: ec.SensorValue, context: grpc.ServicerContext) -> Empty:
        self.f.record("setSensor", request.target)
        self.f.sensors[request.target] = request
        return Empty()

    def setPhysicalModel(self, request: ec.PhysicalModelValue, context: grpc.ServicerContext) -> Empty:
        self.f.record("setPhysicalModel", request.target)
        self.f.physical.append(request)
        return Empty()

    def setClipboard(self, request: ec.ClipData, context: grpc.ServicerContext) -> Empty:
        self.f.record("setClipboard")
        self.f.clipboard = request.text
        return Empty()

    def getClipboard(self, request: Empty, context: grpc.ServicerContext) -> ec.ClipData:
        return ec.ClipData(text=self.f.clipboard)

    def setGps(self, request: ec.GpsState, context: grpc.ServicerContext) -> Empty:
        self.f.record("setGps")
        self.f.gps = request
        return Empty()

    def getGps(self, request: Empty, context: grpc.ServicerContext) -> ec.GpsState:
        return self.f.gps

    def setMicrophoneState(self, request: ec.MicrophoneState, context: grpc.ServicerContext) -> Empty:
        self.f.record("setMicrophoneState", request.realAudioEnabled)
        self.f.microphone = request.realAudioEnabled
        return Empty()

    def getMicrophoneState(self, request: Empty, context: grpc.ServicerContext) -> ec.MicrophoneState:
        return ec.MicrophoneState(realAudioEnabled=self.f.microphone)

    def streamNotification(self, request: Empty, context: grpc.ServicerContext):   # type: ignore[no-untyped-def]
        self.f.record("streamNotification")
        if self.f.booted:
            yield ec.Notification(booted=ec.BootCompletedNotification())
        yield from self._drain(self.f.notifications, context)

    def streamLogcat(self, request: ec.LogMessage, context: grpc.ServicerContext):   # type: ignore[no-untyped-def]
        self.f.record("streamLogcat")
        for item in self._drain(self.f.logcat, context):
            # str = threadtime text, as the real Text mode sends it in `contents` (S5: one entry per message,
            # no newline); entries are kept for older tests, the real Parsed mode sends nothing
            yield ec.LogMessage(contents=item) if isinstance(item, str) else ec.LogMessage(entries=[item])

    def _drain(self, q: "queue.Queue[Any]", context: grpc.ServicerContext):   # type: ignore[no-untyped-def]
        while context.is_active() and not self.f.stopping.is_set():
            try:
                yield q.get(timeout=0.05)
            except queue.Empty:
                continue


class _Snapshots(ss_grpc.SnapshotServiceServicer):
    def __init__(self, fake: "FakeEmulatorServer") -> None:
        self.f = fake

    def _scripted(self, method: str) -> ss.SnapshotPackage | None:
        replies = self.f.snapshot_script.get(method)
        return replies.popleft() if replies else None

    def ListSnapshots(self, request: ss.SnapshotFilter, context: grpc.ServicerContext) -> ss.SnapshotList:
        self.f.record("ListSnapshots")
        return ss.SnapshotList(snapshots=[
            ss.SnapshotDetails(snapshot_id=sid, status=ss.SnapshotDetails.Loaded if sid == self.f.loaded
                               else ss.SnapshotDetails.Compatible)
            for sid in sorted(self.f.snapshots)])

    def SaveSnapshot(self, request: ss.SnapshotPackage, context: grpc.ServicerContext) -> ss.SnapshotPackage:
        self.f.record("SaveSnapshot", request.snapshot_id)
        scripted = self._scripted("SaveSnapshot")
        if scripted is not None:
            return scripted
        # 37.2.12 overwrites an existing id and reports success (S4); a refusal can still be scripted
        self.f.snapshots.add(request.snapshot_id)
        return ss.SnapshotPackage(snapshot_id=request.snapshot_id, success=True)

    def LoadSnapshot(self, request: ss.SnapshotPackage, context: grpc.ServicerContext) -> ss.SnapshotPackage:
        self.f.record("LoadSnapshot", request.snapshot_id)
        scripted = self._scripted("LoadSnapshot")
        if scripted is not None:
            return scripted
        if request.snapshot_id not in self.f.snapshots:
            return ss.SnapshotPackage(snapshot_id=request.snapshot_id, success=False, err=b"not found")
        self.f.loaded = request.snapshot_id
        return ss.SnapshotPackage(snapshot_id=request.snapshot_id, success=True)

    def DeleteSnapshot(self, request: ss.SnapshotPackage, context: grpc.ServicerContext) -> ss.SnapshotPackage:
        self.f.record("DeleteSnapshot", request.snapshot_id)
        self.f.snapshots.discard(request.snapshot_id)   # the real service reports success either way
        return ss.SnapshotPackage(snapshot_id=request.snapshot_id, success=True)


class FakeEmulatorServer:
    """Usage: with FakeEmulatorServer() as fake: channel = make_channel(fake.port, fake.token)."""

    def __init__(self, token: str = "fake-token", *, width: int = 720, height: int = 1280, fps: float = 30.0) -> None:
        self.token = token
        self.width, self.height, self.fps = width, height, fps
        self.port = 0
        self.started = time.monotonic()
        self.stopping = threading.Event()
        self.calls: list[tuple[str, Any]] = []
        self.booted = True
        self.vm_state: ec.VmRunState.RunState = ec.VmRunState.RUNNING
        self.rotation: ec.Rotation.SkinRotation = ec.Rotation.PORTRAIT
        self.seq = 0
        self.input_events: list[ec.InputEvent] = []
        self.sensors: dict[int, ec.SensorValue] = {}
        self.physical: list[ec.PhysicalModelValue] = []
        self.clipboard = ""
        self.gps = ec.GpsState()
        self.microphone = False
        self.notifications: "queue.Queue[ec.Notification]" = queue.Queue()
        self.logcat: "queue.Queue[ec.LogcatEntry | str]" = queue.Queue()
        self.snapshots: set[str] = set()
        self.loaded: str | None = None
        self.snapshot_script: dict[str, deque[ss.SnapshotPackage]] = {}
        self._server: grpc.Server | None = None

    def record(self, method: str, arg: Any = None) -> None:
        self.calls.append((method, arg))

    def script_snapshot(self, method: str, reply: ss.SnapshotPackage) -> None:
        """Queue a reply for SaveSnapshot / LoadSnapshot, used before the default behaviour."""
        self.snapshot_script.setdefault(method, deque()).append(reply)

    def start(self) -> int:
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="fake-emulator"),
                             interceptors=[_TokenCheck(self.token)])
        ec_grpc.add_EmulatorControllerServicer_to_server(_Controller(self), server)
        ss_grpc.add_SnapshotServiceServicer_to_server(_Snapshots(self), server)
        self.port = server.add_insecure_port("127.0.0.1:0")
        try:   # 'localhost' may resolve to ::1 first, like the real token server's bind
            server.add_insecure_port(f"[::1]:{self.port}")
        except RuntimeError:
            pass
        server.start()
        self._server = server
        return self.port

    def stop(self) -> None:
        self.stopping.set()
        if self._server is not None:
            self._server.stop(grace=0.5).wait(5)
            self._server = None

    def __enter__(self) -> "FakeEmulatorServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
