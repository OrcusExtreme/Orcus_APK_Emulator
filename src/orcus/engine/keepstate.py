"""Keep-state (ARCHITECTURE.md §6.3): the phone kept between sessions, in two snapshots used in turn.

The pointer `state\\keep_state.json` names the slot that holds the newest kept phone. A normal stop saves into the
other slot, checks it, moves the pointer, and only then deletes the old slot: a stop that dies halfway leaves the
pointer's slot whole. Fast stops (logoff, low disk) never save. A slot grew from one golden: a rebuilt golden
drops both.
"""

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from google.protobuf.message import DecodeError

from orcus.common import jsonio
from orcus.common.paths import OrcusPaths
from orcus.emugrpc.generated import snapshot_pb2

POINTER = "keep_state.json"
SLOTS = ("a", "b")


@dataclass(frozen=True)
class Pointer:
    slot: str               # "a" | "b"; "" when no phone is kept (only a failed save to report)
    provision_id: str       # the golden the kept phone grew from
    saved_at: float
    once: bool = False      # kept by [keep this time]: the next start opens it with the setting off too
    save_failed: str = ""   # the last stop could not keep the phone: said at the next start


def name(slot: str) -> str:
    return f"orcus_user_{slot}"


def other(slot: str | None) -> str:
    return "b" if slot == "a" else "a"


def slot_dir(paths: OrcusPaths, slot: str) -> Path:
    return paths.avd_data / "snapshots" / name(slot)


def exists(paths: OrcusPaths, slot: str) -> bool:
    return slot in SLOTS and (slot_dir(paths, slot) / "ram.bin").exists()


def gpu(paths: OrcusPaths, slot: str) -> str | None:
    """The host GPU the emulator recorded with the slot, like snapshots.golden_gpu."""
    try:
        snap = snapshot_pb2.Snapshot.FromString((slot_dir(paths, slot) / "snapshot.pb").read_bytes())
    except (OSError, DecodeError):
        return None
    return snap.host.gpu_driver if snap.HasField("host") else None


def read(paths: OrcusPaths) -> Pointer | None:
    data = jsonio.read_json(paths.state_file(POINTER))
    if not isinstance(data, dict):
        return None
    try:
        p = Pointer(str(data["slot"]), str(data["provision_id"]), float(data["saved_at"]), bool(data.get("once")),
                    str(data.get("save_failed", "")))
    except (KeyError, TypeError, ValueError):
        return None
    return p if p.slot in (*SLOTS, "") else None


def write(paths: OrcusPaths, pointer: Pointer) -> None:
    jsonio.write_json_atomic(paths.state_file(POINTER), asdict(pointer))


def drop(paths: OrcusPaths) -> None:
    """The pointer and both slots (no emulator running): the golden they grew from is gone, or keeping is off."""
    paths.state_file(POINTER).unlink(missing_ok=True)
    for slot in SLOTS:
        shutil.rmtree(slot_dir(paths, slot), ignore_errors=True)
