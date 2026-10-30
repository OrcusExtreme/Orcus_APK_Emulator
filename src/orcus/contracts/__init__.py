"""Types shared across packages (ARCHITECTURE.md §3). Standard library only."""

from .apk import (
    ApkInfo,
    ApkService,
    FileKind,
    Issue,
    IssueCode,
    IssueLevel,
    Prompt,
    RunEvent,
    RunPolicy,
    RunStage,
)
from .device import (
    CancellableIter,
    DeviceControls,
    DeviceSession,
    Frame,
    FrameSource,
    HwKey,
    InputSink,
    LogEntry,
    PointerAt,
    Rotation,
    Sensitivity,
)
from .engine import (
    CmdResult,
    DeviceCaps,
    DeviceEndpoint,
    DeviceSessionFactory,
    DeviceShell,
    EngineControl,
    EngineState,
    EngineStatus,
    EngineView,
    EpochCause,
    GpuMode,
    SessionMode,
)
from .errors import EngineNotReady, ErrorCode, OrcusError, OrcusFailure, Remedy, RemedyAction
from .session import (
    ApkSettings,
    ApplyResult,
    EngineSettings,
    FrameSettings,
    HeadlessSession,
    InputSettings,
    PortSettings,
    PrivacySettings,
    ReadOnlyEngine,
    SensorSettings,
    SessionApi,
    SessionSettings,
    Settings,
    UiSettings,
)
from .setup import LicenseText, SetupApi, SetupEvent, SetupStep

__all__ = [
    "ApkInfo", "ApkService", "ApkSettings", "ApplyResult", "CancellableIter", "CmdResult",
    "DeviceCaps", "DeviceControls", "DeviceEndpoint", "DeviceSession", "DeviceSessionFactory",
    "DeviceShell", "EngineControl", "EngineNotReady", "EngineSettings", "EngineState", "EngineStatus",
    "EngineView", "EpochCause", "ErrorCode", "FileKind", "Frame", "FrameSettings", "FrameSource",
    "GpuMode", "HeadlessSession", "HwKey", "InputSettings", "InputSink", "Issue", "IssueCode",
    "IssueLevel", "LicenseText", "LogEntry", "OrcusError", "OrcusFailure", "PointerAt", "PortSettings",
    "PrivacySettings", "Prompt", "ReadOnlyEngine", "Remedy", "RemedyAction", "Rotation", "RunEvent",
    "RunPolicy", "RunStage", "SensorSettings", "Sensitivity", "SessionApi", "SessionMode",
    "SessionSettings", "Settings", "SetupApi", "SetupEvent", "SetupStep", "UiSettings",
]
