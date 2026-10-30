"""A look at a VM another process owns: `orcus-cli engine status|shot` (ARCHITECTURE.md §1.3).

Reads the owner record and the emulator's discovery file, then makes read-only gRPC calls (getStatus,
getScreenshot). The VM, the lock and the owner record are never touched.
"""

import os
from dataclasses import dataclass

import grpc
from google.protobuf import empty_pb2

from orcus.common import jsonio, winjob
from orcus.common.paths import OrcusPaths
from orcus.contracts import EngineState, EngineStatus, EpochCause, SessionMode
from orcus.emugrpc import channel as grpc_channel
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from . import discovery


@dataclass(frozen=True)
class Owner:
    pid: int
    kind: str
    started: float
    console: int
    grpc: int


class ReadOnlyVm:
    """orcus.contracts.ReadOnlyEngine. Mode and epoch belong to the owner, so status() reports them as
    GOLDEN / 0; `owner` and `uptime_s()` say what is known from outside."""

    def __init__(self, owner: Owner, record: discovery.DiscoveryRecord) -> None:
        assert record.grpc_port and record.grpc_token
        self.owner = owner
        self.record = record
        self._channel = grpc_channel.make_channel(record.grpc_port, record.grpc_token)
        self._stub = grpc_channel.controller(self._channel)

    def _status(self) -> object | None:
        try:
            return self._stub.getStatus(empty_pb2.Empty(), timeout=5)
        except grpc.RpcError:
            return None

    def status(self) -> EngineStatus | None:
        st = self._status()
        if st is None:
            return None
        state = EngineState.READY if getattr(st, "booted", False) else EngineState.BOOTING
        return EngineStatus(state, f"engine.state.{state}", None, SessionMode.GOLDEN, 0, EpochCause.BOOT, True, (),
                            None, None)

    def uptime_s(self) -> float | None:
        st = self._status()
        return getattr(st, "uptime", 0) / 1000 if st is not None else None

    def screenshot_png(self) -> bytes | None:
        try:
            img = self._stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.PNG), timeout=15)
        except grpc.RpcError:
            return None
        return bytes(img.image) or None

    def close(self) -> None:
        self._channel.close()


def read_owner(paths: OrcusPaths) -> Owner | None:
    raw = jsonio.read_json(paths.state_file("engine_owner.json"))
    if not isinstance(raw, dict):
        return None
    try:
        return Owner(int(raw["pid"]), str(raw.get("kind", "?")), float(raw.get("started", 0.0)),
                     int(raw["console"]), int(raw["grpc"]))
    except (KeyError, TypeError, ValueError):
        return None


def attach(paths: OrcusPaths) -> ReadOnlyVm | None:
    """None when nobody owns a running VM (the record is stale or the emulator is gone)."""
    owner = read_owner(paths)
    if owner is None or not winjob.pid_alive(owner.pid):
        return None
    dirs = discovery.candidate_dirs({"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""), "TMP": str(paths.tmp)})
    rec = discovery.find(dirs, serial_port=owner.console, avd_dir=paths.avd_data, alive=winjob.pid_alive)
    if rec is None or not rec.grpc_port or not rec.grpc_token:
        return None
    return ReadOnlyVm(owner, rec)
