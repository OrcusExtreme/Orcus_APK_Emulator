"""The surface the GUI and the CLI call: SessionApi, settings and read-only engine access."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from .apk import ApkService
from .device import DeviceSession, Sensitivity
from .engine import EngineControl, EngineStatus, EngineView, GpuMode
from .setup import SetupApi

# Settings sections carry the keys and defaults of ARCHITECTURE.md §11.3. These dataclass defaults
# are the built-in layer (lowest precedence); orcus.common.config merges settings.json, ORCUS_* and
# command-line values on top.


@dataclass(frozen=True)
class PortSettings:
    console: int = 5640
    grpc: int = 8640
    adb_server: int = 15037


@dataclass(frozen=True)
class EngineSettings:
    ram_mb: int = 2560   # the emulator raises API 36 images to 2560 MB anyway (S6, 2026-10-30)
    cores: int = 4
    gpu_mode: GpuMode = GpuMode.HOST   # there is no "auto"
    idle_grpc_timeout_s: int = 120
    keepalive_s: int = 30
    ports: PortSettings = field(default_factory=PortSettings)
    locale: str = "ko-KR"
    timezone: str = "Asia/Seoul"


@dataclass(frozen=True)
class ApkSettings:
    grant_all: bool = False
    reset_before_each_run: bool = False
    staging_keep: int = 20


@dataclass(frozen=True)
class SessionSettings:
    keep_state: bool = False   # [P4]


@dataclass(frozen=True)
class InputSettings:
    paste_method: str = "paste_key"
    esc_is_back: bool = True
    right_click_back: bool = True


@dataclass(frozen=True)
class FrameSettings:
    flip_rows: bool = False
    max_fps: int = 60


def _no_overrides() -> Mapping[str, Sensitivity]:
    return MappingProxyType({})


@dataclass(frozen=True)
class SensorSettings:
    shake_default: Sensitivity = Sensitivity.MODERATE
    shake_sensitivity: Mapping[str, Sensitivity] = field(default_factory=_no_overrides)   # per package
    location_presets: tuple[tuple[float, float, str], ...] = ((37.5665, 126.9780, "서울시청"),)


@dataclass(frozen=True)
class PrivacySettings:
    mic_allowed_packages: tuple[str, ...] = ()


@dataclass(frozen=True)
class UiSettings:
    confirm_close_data: bool = True


@dataclass(frozen=True)
class Settings:
    engine: EngineSettings = field(default_factory=EngineSettings)
    apk: ApkSettings = field(default_factory=ApkSettings)
    session: SessionSettings = field(default_factory=SessionSettings)
    input: InputSettings = field(default_factory=InputSettings)
    frame: FrameSettings = field(default_factory=FrameSettings)
    sensors: SensorSettings = field(default_factory=SensorSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    ui: UiSettings = field(default_factory=UiSettings)


@dataclass(frozen=True)
class ApplyResult:
    needs_restart: bool
    needs_rebuild: bool


class SessionApi(Protocol):
    """The only object the GUI calls. Every method is non-blocking."""

    @property
    def engine(self) -> EngineView: ...
    @property
    def apps(self) -> ApkService: ...
    @property
    def setup(self) -> SetupApi: ...
    def device(self) -> DeviceSession | None: ...   # fetch again when the epoch changes
    def settings(self) -> Settings: ...
    def preview_settings(self, changes: Mapping[str, object]) -> ApplyResult: ...
    # preview_settings: restart/rebuild needs only, nothing is saved
    def update_settings(self, changes: Mapping[str, object]) -> ApplyResult: ...
    # update_settings: dotted keys such as 'engine.gpu_mode'
    def report(self) -> str: ...                    # body of [Copy report]
    def open_paths(self, paths: Sequence[Path]) -> None: ...
    def shutdown(self, *, fast: bool = False) -> None: ...


class ReadOnlyEngine(Protocol):
    """Watches a VM owned by another process without touching it."""

    def status(self) -> EngineStatus | None: ...
    def screenshot_png(self) -> bytes | None: ...


class HeadlessSession(SessionApi, Protocol):
    """CLI only: needs wait_ready/shell and friends."""

    @property
    def engine(self) -> EngineControl: ...
    def attach_readonly(self) -> ReadOnlyEngine | None: ...   # `orcus-cli engine status|shot`
