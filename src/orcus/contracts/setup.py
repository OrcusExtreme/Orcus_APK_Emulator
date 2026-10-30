"""Setup surface shared by `orcus-cli setup` and the [P4] GUI wizard."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Protocol

from .errors import OrcusError


class SetupStep(StrEnum):
    CHECK = auto()
    LICENSE = auto()
    WHPX = auto()
    DOWNLOAD = auto()
    EXTRACT = auto()
    REBOOT = auto()
    ACCEL = auto()
    AVD = auto()
    PROVISION = auto()
    DONE = auto()


@dataclass(frozen=True)
class SetupEvent:
    step: SetupStep
    state: str   # "pending" | "running" | "waiting_user" | "done" | "failed"
    progress: float | None
    bytes_done: int
    bytes_total: int
    rate_bps: float
    eta_s: float | None
    problem: OrcusError | None


@dataclass(frozen=True)
class LicenseText:
    license_id: str
    text: str
    sha256: str
    packages: tuple[str, ...]


class SetupApi(Protocol):
    """The CLI and the [P4] wizard render the same step model. Every method is non-blocking."""

    def events(self) -> tuple[SetupEvent, ...]: ...
    def run(self) -> None: ...   # a step that needs a human decision stops in waiting_user
    def license(self) -> LicenseText | None: ...
    # license: the text run() fetched while LICENSE is waiting_user
    def accept_license(self, sha256: str, accepted_by: str) -> None: ...
    # accept_license: only when a human explicitly agreed; the result arrives as a SetupEvent
    def enable_whpx(self) -> None: ...   # asks for UAC and returns; the result arrives as a SetupEvent
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def subscribe(self, cb: Callable[[SetupEvent], None]) -> Callable[[], None]: ...
