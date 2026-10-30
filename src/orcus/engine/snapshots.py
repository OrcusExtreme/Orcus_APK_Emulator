"""Golden validity (ARCHITECTURE.md §6.4): the key a golden was built against, kept in state\\provision.json.

A golden is reused only when the record's key equals today's key and the snapshot folder exists;
anything else rebuilds. Keys that cannot be predicted (drivers, emulator internals) show up as a failed
forced load instead (§5.1).
"""

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from orcus.common import avd, jsonio, pins
from orcus.common.paths import OrcusPaths
from orcus.contracts import GpuMode, SessionMode, Settings

from . import args

PROVISION_V = 3   # bump when the provisioning steps change (3: stylus, Bluetooth, clipboard toast, flush)
RECORD = "provision.json"


@dataclass(frozen=True)
class ValidityKey:
    emulator_build: str
    image: str
    config_sha256: str
    gpu_mode: str
    locale: str
    timezone: str
    features: str
    provision_v: int
    adbkey_sha256: str


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def current_key(paths: OrcusPaths, settings: Settings) -> ValidityKey:
    return ValidityKey(
        emulator_build=pins.EMULATOR_BUILD,
        image=f"{pins.SYSTEM_IMAGE_API}-{pins.SYSTEM_IMAGE_TAG}-r{pins.SYSTEM_IMAGE.revision}",
        config_sha256=avd.config_sha256(paths) or "",
        gpu_mode=str(settings.engine.gpu_mode),
        locale=settings.engine.locale,
        timezone=settings.engine.timezone,
        features=" ".join(args.FEATURES),
        provision_v=PROVISION_V,
        adbkey_sha256=_sha256(Path(f"{paths.adbkey}.pub")),
    )


def golden_key(key: ValidityKey) -> ValidityKey:
    """host and host_hidden share one golden (S2/S4: both load it); software never has one."""
    if key.gpu_mode == GpuMode.HOST_HIDDEN:
        return ValidityKey(**{**asdict(key), "gpu_mode": str(GpuMode.HOST)})
    return key


@dataclass(frozen=True)
class Record:
    mode: SessionMode
    provision_id: str
    key: ValidityKey
    observed: dict[str, str]


def read(paths: OrcusPaths) -> Record | None:
    data = jsonio.read_json(paths.state_file(RECORD))
    if not isinstance(data, dict):
        return None
    try:
        return Record(SessionMode(data["mode"]), str(data["provision_id"]), ValidityKey(**data["key"]),
                      {str(k): str(v) for k, v in dict(data.get("observed", {})).items()})
    except (KeyError, TypeError, ValueError):
        return None


def write(paths: OrcusPaths, record: Record) -> None:
    jsonio.write_json_atomic(paths.state_file(RECORD), {
        "mode": str(record.mode), "provision_id": record.provision_id, "key": asdict(record.key),
        "observed": record.observed})


def clear(paths: OrcusPaths) -> None:
    paths.state_file(RECORD).unlink(missing_ok=True)


def golden_exists(paths: OrcusPaths) -> bool:
    return (paths.golden_dir / "ram.bin").exists()


def usable(paths: OrcusPaths, key: ValidityKey, mode: SessionMode) -> Record | None:
    """The record when it matches this key and mode (and, for GOLDEN, the snapshot is on disk)."""
    rec = read(paths)
    if rec is None or rec.mode != mode:
        return None
    if mode == SessionMode.GOLDEN:
        return rec if rec.key == golden_key(key) and golden_exists(paths) else None
    return rec if rec.key == key else None
