"""A look at a VM another process owns: `orcus-cli engine status|shot` (ARCHITECTURE.md §1.3).

Reads the owner record and the emulator's discovery file, then makes read-only gRPC calls (getStatus,
getScreenshot). The VM, the lock and the owner record are never touched. The state shown is the one the owner
wrote into its record (3a): the guest's `booted` turns true about 4 s before the owner's SESSION_INIT checks end.
"""

import os
from dataclasses import dataclass, replace

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
    state: EngineState | None = None   # None: written before 3a, without the owner's state
    text_key: str | None = None
    epoch: int = 0
    mode: SessionMode = SessionMode.GOLDEN


class ReadOnlyVm:
    """orcus.contracts.ReadOnlyEngine. status() reports the state, epoch and mode the owner last wrote (read again
    on every call); `owner` and `uptime_s()` say what is known from outside."""

    def __init__(self, owner: Owner, record: discovery.DiscoveryRecord, paths: OrcusPaths) -> None:
        assert record.grpc_port and record.grpc_token
        self.owner = owner
        self.record = record
        self._paths = paths
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
        now = read_owner(self._paths)
        if now is not None and now.pid == self.owner.pid and now.state is not None:
            return EngineStatus(now.state, now.text_key or f"engine.state.{now.state}", None, now.mode, now.epoch,
                                EpochCause.BOOT, True, (), None, None)
        state = EngineState.READY if getattr(st, "booted", False) else EngineState.BOOTING   # an owner from before 3a
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
        owner = Owner(int(raw["pid"]), str(raw.get("kind", "?")), float(raw.get("started", 0.0)),
                      int(raw["console"]), int(raw["grpc"]))
    except (KeyError, TypeError, ValueError):
        return None
    try:   # the owner's own state (3a); a record without it, or with a value we do not know, keeps the defaults
        return replace(owner, state=EngineState(raw["state"]), text_key=str(raw.get("text_key") or "") or None,
                       epoch=int(raw.get("epoch", 0)), mode=SessionMode(raw.get("mode", SessionMode.GOLDEN)))
    except (KeyError, TypeError, ValueError):
        return owner


def attach(paths: OrcusPaths) -> ReadOnlyVm | None:
    """None when nobody owns a running VM (the record is stale or the emulator is gone)."""
    owner = read_owner(paths)
    if owner is None or not winjob.pid_alive(owner.pid):
        return None
    dirs = discovery.candidate_dirs({"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", "")})   # not the shared <home>\tmp
    rec = discovery.find(dirs, serial_port=owner.console, avd_dir=paths.avd_data, alive=winjob.pid_alive)
    if rec is None or not rec.grpc_port or not rec.grpc_token:
        return None
    return ReadOnlyVm(owner, rec, paths)
