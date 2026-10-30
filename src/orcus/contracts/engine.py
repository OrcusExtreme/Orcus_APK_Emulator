"""Engine surface: VM lifecycle status and the adb shell used by orcus.apk."""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Protocol

from .device import DeviceSession
from .errors import ErrorCode, OrcusError


class EngineState(StrEnum):
    NOT_INSTALLED = auto()
    STOPPED = auto()
    PREFLIGHT = auto()
    LAUNCHING = auto()
    CONNECTING = auto()
    BOOTING = auto()
    SESSION_INIT = auto()
    READY = auto()
    RESETTING = auto()
    PROVISIONING = auto()
    STOPPING = auto()
    FAILED = auto()


class SessionMode(StrEnum):
    GOLDEN = auto()
    COLD = auto()
    KEEP_STATE = auto()   # [P4]


class GpuMode(StrEnum):
    HOST = "host"
    HOST_HIDDEN = "host_hidden"
    SOFTWARE = "software"   # [P4] LADDER


class EpochCause(StrEnum):
    BOOT = auto()
    RESET = auto()
    CRASH_RESTART = auto()
    REBUILD = auto()
    RESET_RECOVERY = auto()


@dataclass(frozen=True)
class DeviceCaps:
    """Cached in state\\device_caps.json."""

    api: int
    abis: tuple[str, ...]
    panel: tuple[int, int, int]   # (w, h, dpi) from getStatus.hardwareConfig
    renderer: str
    gpu_mode: GpuMode
    hypervisor: str
    locale: str
    timezone: str
    tts_engine: str | None
    tts_ko: bool | None
    speech_recognizer: bool
    imes: tuple[str, ...]


@dataclass(frozen=True)
class EngineStatus:
    state: EngineState
    text_key: str
    progress: float | None
    mode: SessionMode
    epoch: int                       # +1 only in SESSION_INIT (after boot and after reset)
    epoch_cause: EpochCause          # ApkService reinstall and the GUI crash notice only for CRASH_RESTART
    display_on: bool
    warnings: tuple[ErrorCode, ...]  # warnings while READY (ENGINE_GPU_SOFTWARE ...) are banners, not states
    error: OrcusError | None
    caps: DeviceCaps | None


@dataclass(frozen=True)
class DeviceEndpoint:
    """Not exposed to the GUI (absent from SessionApi)."""

    target: str
    token: str
    serial: str
    epoch: int


type DeviceSessionFactory = Callable[[DeviceEndpoint], DeviceSession]   # app injects device.connect


class EngineView(Protocol):
    """What the GUI sees. Every method only enqueues a command and returns."""

    def status(self) -> EngineStatus: ...
    def start(self) -> None: ...
    def stop(self, *, fast: bool = False) -> None: ...   # completion shows as EngineStatus(STOPPED)
    def reset_device(self) -> int: ...
    # reset_device returns the target epoch (= current + 1); in COLD it becomes a rebuild request
    # (the GUI confirms first)
    def rebuild(self, reason: str) -> None: ...          # only after the user confirmed
    def subscribe(self, cb: Callable[[EngineStatus], None]) -> Callable[[], None]: ...


@dataclass(frozen=True)
class CmdResult:
    argv: tuple[str, ...]
    code: int
    out: str
    err: str
    elapsed_s: float
    timed_out: bool


class DeviceShell(Protocol):
    """The only device surface orcus.apk uses (adb). Bound to one epoch."""

    serial: str
    epoch: int   # using an object from an older epoch raises EngineNotReady

    def get_state(self) -> str: ...
    def shell(self, argv: Sequence[str], timeout_s: float = 30) -> CmdResult: ...
    # shell quotes every argv element for the device shell (shlex.quote). adb only joins arguments
    # with spaces, so this is what lets '' or values with spaces arrive intact.
    def install(self, apk: Path, flags: Sequence[str], timeout_s: float = 180) -> CmdResult: ...
    def uninstall(self, package: str) -> CmdResult: ...


class EngineControl(EngineView, Protocol):
    """Used by apk, app and cli only."""

    def wait_ready(self, timeout_s: float, *, min_epoch: int | None = None,
                   cancel: threading.Event | None = None) -> EngineStatus: ...
    # Returns when READY with epoch >= min_epoch. Cancelled -> CancelledError.
    # FAILED, NOT_INSTALLED (PRE_NOT_INSTALLED), or STOPPED with no pending start/restart -> OrcusFailure.
    # Calling it on the supervisor thread fails an assert.
    def shell(self) -> DeviceShell: ...        # bound to the current epoch; EngineNotReady unless READY
    def session(self) -> DeviceSession: ...    # same object as SessionApi.device()
