"""AVD files (ARCHITECTURE.md §11.2). The only writer of orcus36.ini and config.ini.

Any key change changes the config.ini hash, which is part of the golden validity key, so the
golden snapshot gets rebuilt.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from orcus.contracts import EngineSettings

from .paths import AVD_NAME, OrcusPaths
from .pins import SYSTEM_IMAGE, SYSTEM_IMAGE_API, SYSTEM_IMAGE_TAG

USERDATA_GIB = 6


def avd_ini_text(paths: OrcusPaths) -> str:
    return (
        "avd.ini.encoding=UTF-8\n"
        f"path={paths.avd_data}\n"
        f"target=android-{SYSTEM_IMAGE_API}\n"
    )


def config_ini_text(engine: EngineSettings) -> str:
    lines = [
        "hw.cpu.arch=x86_64",            # required: the default is arm
        "abi.type=x86_64",
        f"image.sysdir.1={SYSTEM_IMAGE.install_dir}\\",
        f"tag.id={SYSTEM_IMAGE_TAG}",
        "hw.lcd.width=720",
        "hw.lcd.height=1280",
        "hw.lcd.density=320",
        f"hw.ramSize={engine.ram_mb}",
        f"hw.cpu.ncore={engine.cores}",
        f"disk.dataPartition.size={USERDATA_GIB}G",
        "hw.keyboard=yes",
        "hw.mainKeys=yes",               # the guest shows its nav bar anyway (S7); bezel keys send Back/Home/Recents
        "hw.gpu.enabled=yes",
        "hw.gpu.mode=host",              # the command line -gpu decides the real mode
        "hw.audioInput=yes",
        "hw.audioOutput=yes",
        "hw.camera.back=emulated",
        "hw.camera.front=none",
        "PlayStore.enabled=false",
        "fastboot.forceColdBoot=no",
        "firstboot.saveToLocalSnapshot=no",
        "firstboot.bootFromLocalSnapshot=no",
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class AvdWriteResult:
    changed: bool
    config_sha256: str


def _write_if_different(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    path.write_bytes(data)
    return True


def write_avd(paths: OrcusPaths, engine: EngineSettings) -> AvdWriteResult:
    """Idempotent: files with identical bytes are left alone."""
    paths.avd_data.mkdir(parents=True, exist_ok=True)
    config = config_ini_text(engine)
    changed = _write_if_different(paths.avd_ini, avd_ini_text(paths))
    changed = _write_if_different(paths.avd_data / "config.ini", config) or changed
    return AvdWriteResult(changed, hashlib.sha256(config.encode("utf-8")).hexdigest())


def config_sha256(paths: OrcusPaths) -> str | None:
    try:
        return hashlib.sha256((paths.avd_data / "config.ini").read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def avd_name_is_valid(name: str = AVD_NAME) -> bool:
    return bool(name) and all(c.isascii() and (c.isalnum() or c in "_.-") for c in name)
