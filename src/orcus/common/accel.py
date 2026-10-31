"""WHPX state and `emulator-check accel` (ARCHITECTURE.md §11.6 steps 1 and 7).

Shared by setup (install) and engine (PREFLIGHT). On this PC WinHvPlatform.dll exists and
WHvGetCapability(HypervisorPresent) returns 1 although the feature is off, so neither is used.
"""

import re
import winreg
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum

from orcus.contracts import CmdResult, ErrorCode

from . import proc, win32

REBOOT_PENDING_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"
INSTALL_STATE_COMMAND = (
    "(Get-CimInstance -ClassName Win32_OptionalFeature -Filter \"Name='HypervisorPlatform'\").InstallState"
)
BOOT_TIME_TOLERANCE_S = 30.0

type Runner = Callable[..., CmdResult]


class WhpxState(IntEnum):
    ENABLED = 1
    DISABLED = 2
    ABSENT = 3
    UNKNOWN = 4


def powershell_exe() -> str:
    return str(win32.system_dir() / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def parse_install_state(output: str) -> WhpxState | None:
    match = re.search(r"\b([1-4])\b", output)
    return WhpxState(int(match.group(1))) if match else None


def query_install_state(run: Runner = proc.run) -> WhpxState | None:
    """Win32_OptionalFeature 'HypervisorPlatform'.InstallState; no admin needed (~0.7 s). None = query failed."""
    result = run([powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", INSTALL_STATE_COMMAND],
                 timeout_s=30)
    if result.timed_out or result.code != 0:
        return None
    return parse_install_state(result.out)


def reboot_pending() -> bool:
    """CBS RebootPending: *some* servicing work waits for a reboot (maybe Windows Update).

    Only used to explain an accel failure, never to decide the WHPX state.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REBOOT_PENDING_KEY):
            return True
    except OSError:
        return False


@dataclass(frozen=True)
class AccelReport:
    usable: bool
    hypervisor: str | None   # "WHPX", "AEHD", ... as named by emulator-check
    code: int
    text: str


def parse_emulator_check(code: int, output: str) -> AccelReport:
    """`emulator-check accel` alone. Usable means 'WHPX(' and 'is installed and usable' appear.

    The exit code is recorded but not trusted: combined checks print one block per check, and the
    hyper-v block returns 2 on this PC.
    """
    text = output.strip()
    match = re.search(r"\b(WHPX|AEHD|HAXM|KVM|HVF)\(", text)
    hypervisor = match.group(1) if match else None
    usable = hypervisor == "WHPX" and "is installed and usable" in text
    return AccelReport(usable, hypervisor, code, text)


def run_emulator_check(check_exe: str, env: Mapping[str, str], run: Runner = proc.run) -> AccelReport:
    result = run([check_exe, "accel"], timeout_s=60, env=env)
    return parse_emulator_check(result.code, result.out + ("\n" + result.err if result.err else ""))


def classify_accel_failure(*, reboot_is_pending: bool, dism_succeeded_at_boot: float | None,
                           current_boot: float, dism_boot_id: int | None = None,
                           current_boot_id: int | None = None) -> ErrorCode:
    """Why the final accel check failed (§11.6 step 7)."""
    if reboot_is_pending:
        return ErrorCode.SETUP_REBOOT_PENDING
    if dism_succeeded_at_boot is not None and not same_boot(dism_succeeded_at_boot, current_boot,
                                                            dism_boot_id, current_boot_id):
        return ErrorCode.SETUP_WHPX_REVERTED   # enabled, rebooted, and it is off again
    return ErrorCode.SETUP_ACCEL_FAILED


def same_boot(a: float, b: float, a_id: int | None = None, b_id: int | None = None) -> bool:
    """Boot ids decide when both are known (win32.boot_id); boot times (±30 s) are the fallback."""
    if a_id is not None and b_id is not None:
        return a_id == b_id
    return abs(a - b) <= BOOT_TIME_TOLERANCE_S
