"""Folder permissions (3a review; the user's decision on 2026-10-31): the runtime folder belongs to one account.

D:\\ grants "Authenticated Users: Modify" and new folders inherit it, so on a shared lab PC another account could
replace the SDK binaries (adb.exe, qemu, their DLLs) and have them run as whoever starts Orcus next. Setup cuts
the root off from its parent's permissions and gives full control to this user, SYSTEM and Administrators only.
"""

from pathlib import Path

from orcus.contracts import CmdResult

from . import proc, win32

SYSTEM, ADMINISTRATORS = "S-1-5-18", "S-1-5-32-544"


def lock_to_user(path: Path, sid: str) -> CmdResult:
    """Full control for `sid`, SYSTEM and Administrators, inherited by everything below; nothing from the parent
    any more. Undo with `icacls <path> /reset /T`."""
    grants = [f"*{who}:(OI)(CI)F" for who in (sid, SYSTEM, ADMINISTRATORS)]
    return proc.run([str(win32.system_dir() / "icacls.exe"), str(path), "/inheritance:r", "/grant:r", *grants, "/Q"],
                    timeout_s=600)
