"""Install (ARCHITECTURE.md §7.5): `adb install -r` plus the failure table. Owns the install shell commands."""

import re
from dataclasses import dataclass
from pathlib import Path

from orcus.contracts import CmdResult, DeviceShell, ErrorCode

BYPASS_LOW_TARGET = "--bypass-low-target-sdk-block"
FAILURE = re.compile(r"Failure \[([A-Z0-9_]+)")


@dataclass(frozen=True)
class InstallOutcome:
    ok: bool
    code: ErrorCode | None      # None when ok, or when the caller should ask (INCOMPATIBLE)
    detail: str
    seconds: float
    needs_uninstall_prompt: bool = False


_MAP: tuple[tuple[str, ErrorCode], ...] = (
    ("INSTALL_FAILED_NO_MATCHING_ABIS", ErrorCode.INSTALL_NO_ABI),
    ("INSTALL_FAILED_OLDER_SDK", ErrorCode.INSTALL_OLDER_SDK),
    ("INSTALL_FAILED_INSUFFICIENT_STORAGE", ErrorCode.INSTALL_STORAGE),
    ("INSTALL_PARSE_FAILED", ErrorCode.INSTALL_INVALID),
    ("INVALID_APK", ErrorCode.INSTALL_INVALID),
)


def flags(*, grant_all: bool, target_sdk: int) -> list[str]:
    out = ["-r"]
    if grant_all:
        out.append("-g")
    if target_sdk < 24:
        out.append(BYPASS_LOW_TARGET)
    return out


def _text(r: CmdResult) -> str:
    return (r.out + "\n" + r.err).strip()


def install(shell: DeviceShell, apk: Path, *, grant_all: bool, target_sdk: int) -> InstallOutcome:
    """Runs the retries of §7.5 itself (deprecated SDK, downgrade, offline). INCOMPATIBLE asks the user.

    EngineNotReady from the epoch-bound shell (the phone restarted meanwhile) propagates to the caller.
    """
    fl = flags(grant_all=grant_all, target_sdk=target_sdk)
    total = 0.0
    tried_offline = False
    while True:
        r = shell.install(apk, fl)
        total += r.elapsed_s
        text = _text(r)
        if r.code == 0 and "Success" in text:
            return InstallOutcome(True, None, text[-200:], total)
        # the exact `Failure [CODE` when there is one: the rest of the output can echo manifest text
        m = FAILURE.search(text)
        failure = m.group(1) if m else ""
        if failure == "INSTALL_FAILED_DEPRECATED_SDK_VERSION" and BYPASS_LOW_TARGET not in fl:
            fl.append(BYPASS_LOW_TARGET)
            continue
        if failure == "INSTALL_FAILED_VERSION_DOWNGRADE" and "-d" not in fl:
            fl.append("-d")   # userdebug image allows it
            continue
        if failure == "INSTALL_FAILED_UPDATE_INCOMPATIBLE":
            return InstallOutcome(False, None, text[-300:], total, needs_uninstall_prompt=True)
        lost = r.timed_out or "device offline" in text or "no devices" in text
        if lost and not tried_offline:
            tried_offline = True
            if shell.get_state() == "device":
                continue
        if lost:   # gone again after the one retry: the ADB-lost advice, not "see the log" (§7.5)
            return InstallOutcome(False, ErrorCode.ENGINE_ADB_LOST, text[-300:], total)
        for needle, code in _MAP:
            if needle in (failure or text):
                return InstallOutcome(False, code, text[-300:], total)
        return InstallOutcome(False, ErrorCode.INSTALL_OTHER, text[-300:], total)


def incompatible_package(detail: str) -> str | None:
    """`Existing package <pkg> signatures do not match newer version` names what to uninstall."""
    m = re.search(r"Existing package (\S+) signatures", detail)
    return m.group(1) if m else None


def third_party(shell: DeviceShell) -> frozenset[str]:
    """`pm list packages -3 -f` lines. A reinstall moves the APK to a new /data/app directory, so the
    before/after difference names the package even when the manifest could not be parsed (§7.3)."""
    out = shell.shell(["pm", "list", "packages", "-3", "-f"], timeout_s=30).out
    return frozenset(line.strip() for line in out.splitlines() if line.startswith("package:"))


def apks(lines: frozenset[str]) -> dict[str, str]:
    """package -> its APK in the phone, from `third_party` lines (`package:/data/app/~~x/<pkg>-y/base.apk=<pkg>`)."""
    out: dict[str, str] = {}
    for line in lines:
        path, _, package = line.removeprefix("package:").rpartition("=")
        if path and package:
            out[package] = path
    return out


def installed_package(before: frozenset[str], after: frozenset[str]) -> str | None:
    names = {line.rsplit("=", 1)[-1] for line in after - before}
    return names.pop() if len(names) == 1 else None
