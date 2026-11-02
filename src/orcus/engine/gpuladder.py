"""GPU auto ladder (ARCHITECTURE.md §5.6): host -> host_hidden -> software, one step at a time.

A step down takes a software renderer twice in a row: in a boot and again in the re-check boot right after it
(a provisioning boot counts at once: it is long, and a golden cannot be made on a software renderer). The level
is kept in state\\gpu.json for (emulator build, display adapter, remote session); a step taken in a remote session
is not kept, since the session is passing. [Retry GPU acceleration] starts at host again.
"""

from dataclasses import asdict, dataclass

from orcus.common import jsonio, pins, win32
from orcus.common.paths import OrcusPaths
from orcus.contracts import GpuMode

FILE = "gpu.json"
LEVELS = (GpuMode.HOST, GpuMode.HOST_HIDDEN, GpuMode.SOFTWARE)


@dataclass(frozen=True)
class Key:
    emulator_build: str
    adapter: str      # the display adapter this session sees (EnumDisplayDevices)
    remote: bool      # Remote Desktop and the like (SM_REMOTESESSION)


def current_key() -> Key:
    return Key(pins.EMULATOR_BUILD, win32.display_adapter(), win32.remote_session())


def level(paths: OrcusPaths, key: Key) -> GpuMode:
    data = jsonio.read_json(paths.state_file(FILE))
    try:
        if isinstance(data, dict) and Key(**data["key"]) == key and GpuMode(data["level"]) in LEVELS:
            return GpuMode(data["level"])
    except (KeyError, TypeError, ValueError):
        pass
    return GpuMode.HOST


def save(paths: OrcusPaths, key: Key, at: GpuMode) -> None:
    jsonio.write_json_atomic(paths.state_file(FILE), {"key": asdict(key), "level": at.value})


def reset(paths: OrcusPaths) -> None:
    paths.state_file(FILE).unlink(missing_ok=True)


def below(at: GpuMode) -> GpuMode | None:
    i = LEVELS.index(at)
    return LEVELS[i + 1] if i + 1 < len(LEVELS) else None
