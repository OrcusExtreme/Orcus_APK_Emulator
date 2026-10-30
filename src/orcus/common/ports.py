"""Port selection (ARCHITECTURE.md §5.3). Test-binds both 127.0.0.1 and ::1."""

import errno
import socket
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from orcus.contracts import ErrorCode, OrcusError, OrcusFailure, PortSettings

from . import jsonio, proc, win32
from .paths import OrcusPaths

CONSOLE_PORTS = tuple(range(5640, 5683, 2))   # outside the 5555-5585 range other adb servers scan
GRPC_PORTS = tuple(range(8640, 8660))
ADB_SERVER_PORTS = tuple(range(15037, 15048))   # 5037 is never used or killed
_UNAVAILABLE_FAMILY = {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, 10047, 10049}


@dataclass(frozen=True)
class Ports:
    console: int
    adb: int          # console + 1
    grpc: int
    adb_server: int

    @property
    def serial(self) -> str:
        return f"emulator-{self.console}"


def can_bind(port: int, hosts: Iterable[tuple[socket.AddressFamily, str]] = (
        (socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1"))) -> bool:
    """False when something listens there or the port is in a Hyper-V/WinNAT excluded range."""
    for family, host in hosts:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.bind((host, port))
        except OSError as e:
            if e.errno in _UNAVAILABLE_FAMILY or getattr(e, "winerror", None) in _UNAVAILABLE_FAMILY:
                continue   # IPv6 disabled: nothing can conflict there
            return False
    return True


def _first(candidates: Iterable[int], ok: Callable[[int], bool]) -> int | None:
    return next((p for p in candidates if ok(p)), None)


def _ordered(preferred: int, candidates: tuple[int, ...]) -> list[int]:
    return [preferred, *(p for p in candidates if p != preferred)]


def choose(preferred: PortSettings, saved: Ports | None = None, *,
           bindable: Callable[[int], bool] = can_bind) -> Ports:
    """Keep the saved ports while they are free (stable serial); otherwise the first free set."""
    if saved and all(bindable(p) for p in (saved.console, saved.adb, saved.grpc, saved.adb_server)):
        return saved
    console = _first(_ordered(preferred.console, CONSOLE_PORTS), lambda p: bindable(p) and bindable(p + 1))
    grpc = _first(_ordered(preferred.grpc, GRPC_PORTS), bindable)
    adb_server = _first(_ordered(preferred.adb_server, ADB_SERVER_PORTS), bindable)
    if console is None or grpc is None or adb_server is None:
        raise OrcusFailure(OrcusError(ErrorCode.PRE_PORTS, f"error.{ErrorCode.PRE_PORTS}",
                                      detail=f"console={console} grpc={grpc} adb_server={adb_server}"))
    return Ports(console, console + 1, grpc, adb_server)


def load_saved(paths: OrcusPaths) -> Ports | None:
    data = jsonio.read_json(paths.state_file("ports.json"))
    if not isinstance(data, dict):
        return None
    try:
        return Ports(int(data["console"]), int(data["adb"]), int(data["grpc"]), int(data["adb_server"]))
    except (KeyError, TypeError, ValueError):
        return None


def save(paths: OrcusPaths, ports: Ports) -> None:
    jsonio.write_json_atomic(paths.state_file("ports.json"), asdict(ports))


def excluded_port_ranges() -> str:
    """Diagnostics only: `netsh int ipv4 show excludedportrange protocol=tcp`."""
    netsh = str(win32.system_dir() / "netsh.exe")
    result = proc.run([netsh, "int", "ipv4", "show", "excludedportrange", "protocol=tcp"], timeout_s=10)
    return result.out.strip() or result.err.strip()
