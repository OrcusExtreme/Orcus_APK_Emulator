"""APK lifecycle surface: intake, manifest facts, preflight issues, runs and their events."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path
from typing import Protocol

from .device import LogEntry
from .errors import ErrorCode, OrcusError


class FileKind(StrEnum):
    APK = auto()
    AAB = auto()
    AIA = auto()
    AIX = auto()
    NOT_ANDROID = auto()


@dataclass(frozen=True)
class ApkInfo:
    sha256: str
    staged: Path
    package: str
    launcher: str | None
    label: str
    launcher_orientation: str | None   # android:screenOrientation of the launcher activity
    version_code: int
    version_name: str | None
    min_sdk: int
    target_sdk: int
    permissions: frozenset[str]
    queries: frozenset[str]
    features: frozenset[str]
    native_abis: frozenset[str]
    asset_elf: bool
    is_app_inventor: bool
    is_companion: bool
    ai_era: str | None
    icon_png: bytes | None


class IssueLevel(StrEnum):
    BLOCK = auto()
    WARN = auto()
    INFO = auto()


class IssueCode(StrEnum):
    HW_NFC = auto()
    HW_BLUETOOTH = auto()
    HW_SERIAL = auto()
    NEEDS_MIC = auto()
    SCANNER = auto()
    LEGACY = auto()
    ARM_TRANSLATION = auto()
    VIBRATE = auto()
    LOCATION = auto()
    ORIENTATION_LOCKED = auto()
    TTS_STATUS = auto()
    SPEECH_STATUS = auto()


@dataclass(frozen=True)
class Issue:
    level: IssueLevel
    code: ErrorCode | IssueCode   # ErrorCode when level is BLOCK
    text_key: str


class RunPolicy(StrEnum):
    KEEP_DATA = auto()
    FRESH_APP = auto()
    FRESH_PHONE = auto()


class RunStage(StrEnum):
    STAGING = auto()
    INSPECTING = auto()
    CHECKING = auto()
    WAITING_ENGINE = auto()
    INSTALLING = auto()
    LAUNCHING = auto()
    RUNNING = auto()
    EXITED = auto()
    CRASHED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class Prompt:
    run_id: str
    prompt_id: str
    text_key: str
    choices: tuple[str, ...]


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    stage: RunStage
    info: ApkInfo | None
    issues: tuple[Issue, ...]
    problem: OrcusError | None
    prompt: Prompt | None
    elapsed_s: float
    pid: int | None
    epoch: int


class ApkService(Protocol):
    """Every method only enqueues a command and returns."""

    def submit(self, path: Path, policy: RunPolicy | None = None) -> str: ...
    # policy None = settings default; the newest drop wins
    def answer(self, run_id: str, prompt_id: str, choice: str) -> None: ...
    def cancel(self, run_id: str) -> None: ...
    def rerun(self) -> None: ...
    def stop_app(self) -> None: ...
    def clear_data(self) -> None: ...
    def uninstall(self) -> None: ...
    def grant_all(self) -> None: ...
    def match_orientation(self) -> None: ...   # [Match orientation]
    def subscribe(self, cb: Callable[[RunEvent], None]) -> Callable[[], None]: ...
    def subscribe_log(self, cb: Callable[[LogEntry], None], *, app_only: bool = True) -> Callable[[], None]: ...
