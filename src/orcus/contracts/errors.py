"""Error codes, remedies and the single exception type that crosses package boundaries."""

from dataclasses import dataclass
from enum import StrEnum, auto


class ErrorCode(StrEnum):
    # SETUP_*: installation (orcus-cli setup, [P4] wizard)
    SETUP_PATH = auto()
    SETUP_DISK = auto()
    SETUP_NETWORK = auto()
    SETUP_CHECKSUM = auto()
    SETUP_LICENSE_CHANGED = auto()
    SETUP_LICENSE_NOT_STABLE = auto()
    SETUP_UAC_DENIED = auto()
    SETUP_DISM_FAILED = auto()
    SETUP_REBOOT_PENDING = auto()
    SETUP_WHPX_REVERTED = auto()
    SETUP_QUARANTINE = auto()
    SETUP_ACCEL_FAILED = auto()
    # PRE_*: checks before the emulator starts
    PRE_NOT_INSTALLED = auto()
    PRE_WHPX_OFF = auto()
    PRE_SDK_MISSING = auto()
    PRE_DISK = auto()
    PRE_PATH = auto()
    PRE_PORTS = auto()
    # ENGINE_*: the emulator
    ENGINE_IN_USE = auto()
    ENGINE_EXIT = auto()
    ENGINE_AVD_LOCKED = auto()
    ENGINE_CONNECT_TIMEOUT = auto()
    ENGINE_BOOT_TIMEOUT = auto()
    ENGINE_GOLDEN_LOAD_FAILED = auto()
    ENGINE_RESET_FAILED = auto()
    ENGINE_PROVISION_FAILED = auto()
    ENGINE_GPU_SOFTWARE = auto()
    ENGINE_ADB_LOST = auto()
    ENGINE_CRASHED = auto()
    ENGINE_UNAVAILABLE = auto()
    ENGINE_NO_FRAMES = auto()
    ENGINE_DISK_LOW = auto()
    # FILE_* / STAGE_*: the dropped file
    FILE_NOT_ANDROID = auto()
    FILE_IS_AAB = auto()
    FILE_IS_AIA = auto()
    FILE_IS_AIX = auto()
    STAGE_DRIVE_TIMEOUT = auto()
    STAGE_COPY_FAILED = auto()
    APK_PARSE = auto()
    # INSTALL_*: installing the app
    INSTALL_OLDER_SDK = auto()
    INSTALL_NO_ABI = auto()
    INSTALL_INCOMPATIBLE = auto()
    INSTALL_STORAGE = auto()
    INSTALL_INVALID = auto()
    INSTALL_OTHER = auto()
    # APP_*: while the app runs
    APP_LAUNCH_NOT_FOUND = auto()
    APP_CRASH = auto()
    APP_ANR = auto()
    APP_DIED = auto()
    APP_AI_COMPONENT_ERROR = auto()
    APP_AI_RUNTIME_ERROR = auto()
    APP_TTS_MISSING = auto()
    APP_SPEECH_MISSING = auto()


class RemedyAction(StrEnum):
    RETRY = auto()
    ENABLE_WHPX = auto()
    COPY_IT_REQUEST = auto()
    REPAIR_SETUP = auto()
    OPEN_LOG = auto()
    RESET_PHONE = auto()
    RUN_AGAIN = auto()
    GRANT_ALL = auto()
    CHANGE_GPU_MODE = auto()
    FREE_DISK = auto()
    CONTINUE_ANYWAY = auto()


@dataclass(frozen=True)
class Remedy:
    action: RemedyAction
    label_key: str


@dataclass(frozen=True)
class OrcusError:
    """One primary remedy. [Log] / [Details] / [Copy report] are added by the GUI per channel."""

    code: ErrorCode
    message_key: str
    detail: str = ""
    remedy: Remedy | None = None
    log_excerpt: str = ""


class OrcusFailure(Exception):
    """The only exception that crosses package boundaries; grpc/subprocess errors are converted inside."""

    error: OrcusError

    def __init__(self, error: OrcusError) -> None:
        super().__init__(f"{error.code}: {error.detail}" if error.detail else str(error.code))
        self.error = error


class EngineNotReady(OrcusFailure):
    pass
