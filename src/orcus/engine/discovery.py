"""Emulator discovery files `pid_<PID>.ini` (ARCHITECTURE.md §5.7). Pure parsing plus a scan.

Location: %LOCALAPPDATA%\\Temp\\avd\\running, even though we change TMP (S6: <TMP>\\avd\\running stays unused).
Match on port.serial: the PID in the file name is the qemu child's (S6), not emulator.exe's.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^pid_(\d+)\.ini$", re.IGNORECASE)
# the emulator's own processes: what a stop or a boot check may end under the SDK (never adb, emulator-check)
EMULATOR_IMAGES = frozenset({"emulator.exe", "qemu-system-x86_64-headless.exe", "qemu-system-x86_64.exe",
                             "netsimd.exe", "crashpad_handler.exe"})


@dataclass(frozen=True)
class DiscoveryRecord:
    pid: int
    serial_port: int
    adb_port: int | None
    grpc_port: int | None
    grpc_token: str | None
    avd_name: str | None
    avd_dir: str | None
    cmdline: str | None
    source: Path


def _int(value: str | None) -> int | None:
    return int(value) if value is not None and value.strip().isdigit() else None


def parse(text: str, pid: int, source: Path) -> DiscoveryRecord | None:
    """None while the file is incomplete (no port.serial yet) or not a discovery file."""
    text = text.lstrip("﻿")
    complete = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    if not complete and lines:
        lines = lines[:-1]   # last line may still be being written
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    serial = _int(values.get("port.serial"))
    if serial is None:
        return None
    return DiscoveryRecord(pid, serial, _int(values.get("port.adb")), _int(values.get("grpc.port")),
                           values.get("grpc.token") or None, values.get("avd.name"), values.get("avd.dir"),
                           values.get("cmdline"), source)


def candidate_dirs(env: Mapping[str, str]) -> list[Path]:
    dirs: list[Path] = []
    local = env.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Temp" / "avd" / "running")
    for var in ("TMP", "TEMP"):
        if env.get(var):
            dirs.append(Path(env[var]) / "avd" / "running")
    unique: list[Path] = []
    for d in dirs:
        if all(str(d).lower() != str(u).lower() for u in unique):
            unique.append(d)
    return unique


def _same_dir(a: str | None, b: Path) -> bool:
    return a is not None and Path(a).as_posix().rstrip("/").lower() == b.as_posix().rstrip("/").lower()


def read_all(dirs: Iterable[Path]) -> list[DiscoveryRecord]:
    records: list[DiscoveryRecord] = []
    for d in dirs:
        try:
            files = list(d.iterdir())
        except OSError:
            continue
        for f in files:
            m = _NAME.match(f.name)
            if not m:
                continue
            try:
                text = f.read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue   # being written or already deleted
            rec = parse(text, int(m.group(1)), f)
            if rec is not None:
                records.append(rec)
    return records


def find(dirs: Iterable[Path], *, serial_port: int, avd_dir: Path,
         alive: Callable[[int], bool]) -> DiscoveryRecord | None:
    """Our emulator: matching serial, our AVD folder, live PID. Others are ignored."""
    for rec in read_all(dirs):
        if rec.serial_port == serial_port and _same_dir(rec.avd_dir, avd_dir) and alive(rec.pid):
            return rec
    return None


def ours(dirs: Iterable[Path], *, avd_dir: Path) -> list[DiscoveryRecord]:
    """Every record that points at our AVD folder (orphan cleanup, §5.7)."""
    return [r for r in read_all(dirs) if _same_dir(r.avd_dir, avd_dir)]
