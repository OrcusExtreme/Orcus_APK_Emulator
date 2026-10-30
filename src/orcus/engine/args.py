"""Emulator command line (ARCHITECTURE.md §5.2). Pure functions; the only owner of flag strings."""

from dataclasses import dataclass
from enum import StrEnum, auto
from pathlib import Path

from orcus.common.paths import AVD_NAME, GOLDEN_SNAPSHOT
from orcus.contracts import GpuMode

FEATURES = ("-feature", "-Vulkan")   # changing -feature invalidates snapshots: fixed
CONSOLE_PORT_RANGE = range(5586, 5683)
MAX_SERVICE_PORT = 49152


class LaunchMode(StrEnum):
    GOLDEN = auto()       # default session
    COLD = auto()         # GPU software: always a cold boot, no snapshots
    PROVISION = auto()    # first preparation
    REBUILD = auto()      # PROVISION + -wipe-data
    KEEP_STATE = auto()   # [P4] orcus_user_a|b without forced load


@dataclass(frozen=True)
class LaunchConfig:
    emulator_exe: Path
    console_port: int
    grpc_port: int
    idle_grpc_timeout_s: int = 120
    timezone: str = "Asia/Seoul"
    locale: str = "ko-KR"
    http_proxy: str | None = None
    avd_name: str = AVD_NAME


_GPU_FLAGS: dict[GpuMode, tuple[str, ...]] = {
    GpuMode.HOST: ("-no-window", "-gpu", "host"),
    GpuMode.HOST_HIDDEN: ("-qt-hide-window", "-gpu", "host"),
    GpuMode.SOFTWARE: ("-no-window", "-gpu", "software"),
}


def mode_flags(mode: LaunchMode, cfg: LaunchConfig, keep_slot: str | None = None) -> tuple[str, ...]:
    match mode:
        case LaunchMode.GOLDEN:
            return ("-snapshot", GOLDEN_SNAPSHOT, "-force-snapshot-load", "-no-snapshot-save")
        case LaunchMode.COLD:
            return ("-no-snapshot-load", "-no-snapshot-save")
        case LaunchMode.PROVISION:
            return ("-no-snapshot-load", "-no-snapshot-save", "-change-locale", cfg.locale)
        case LaunchMode.REBUILD:
            return ("-no-snapshot-load", "-no-snapshot-save", "-change-locale", cfg.locale, "-wipe-data")
        case LaunchMode.KEEP_STATE:
            if keep_slot not in ("a", "b"):
                raise ValueError("KEEP_STATE needs slot 'a' or 'b'")
            return ("-snapshot", f"orcus_user_{keep_slot}", "-no-snapshot-save")


def build(cfg: LaunchConfig, mode: LaunchMode, gpu: GpuMode, *, keep_slot: str | None = None) -> list[str]:
    argv = [
        str(cfg.emulator_exe), "-avd", cfg.avd_name, *_GPU_FLAGS[gpu],
        "-port", str(cfg.console_port),
        "-grpc", str(cfg.grpc_port), "-grpc-use-token",
        "-idle-grpc-timeout", str(cfg.idle_grpc_timeout_s),
        "-no-boot-anim", "-no-metrics",
        "-timezone", cfg.timezone,
        *FEATURES,
    ]
    if cfg.http_proxy:
        argv += ["-http-proxy", cfg.http_proxy]
    argv += mode_flags(mode, cfg, keep_slot)
    problems = invariant_violations(argv)
    if problems:
        raise ValueError("; ".join(problems))
    return argv


def _value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def invariant_violations(argv: list[str]) -> list[str]:
    """§5.2 invariants. build() refuses to return an argv that breaks any of them."""
    bad: list[str] = []
    if "-grpc" in argv and "-grpc-use-token" not in argv:
        bad.append("-grpc without -grpc-use-token opens [::] without auth")
    if "-ports" in argv:
        bad.append("-ports is not used")
    gpu = _value(argv, "-gpu")
    if gpu not in ("host", "software"):
        bad.append(f"-gpu must be host or software, not {gpu}")
    if "-snapshot" in argv and "-no-snapshot-save" not in argv:
        bad.append("-snapshot requires -no-snapshot-save")
    port = _value(argv, "-port")
    if port is None or not port.isdigit() or int(port) % 2 or int(port) not in CONSOLE_PORT_RANGE:
        bad.append(f"console port {port} must be even within 5586-5682")
    grpc = _value(argv, "-grpc")
    if grpc is None or not grpc.isdigit() or int(grpc) >= MAX_SERVICE_PORT:
        bad.append(f"grpc port {grpc} must be below {MAX_SERVICE_PORT}")
    if _value(argv, "-feature") != "-Vulkan":
        bad.append("-feature -Vulkan is always set")
    if "-timezone" not in argv:
        bad.append("-timezone is always set (37.2.12 guests are GMT otherwise)")
    return bad
