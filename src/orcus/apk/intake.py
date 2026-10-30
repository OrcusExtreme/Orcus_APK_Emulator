"""Intake (ARCHITECTURE.md §7.1–§7.2): what was dropped, by content; then an ASCII-named staged copy.

Unicode and Google Drive paths are read only through Python's Unicode file APIs; adb and the other tools only
ever see `<home>\\staging\\<sha256>.apk`. An "online only" Drive file starts downloading when it is read, so
the copy runs on a helper thread with a deadline.
"""

import hashlib
import shutil
import threading
import zipfile
from pathlib import Path

from orcus.common import log, messages_ko
from orcus.contracts import ErrorCode, FileKind, OrcusFailure

_log = log.get("apk.intake")
DRIVE_TIMEOUT_S = 120.0
CHUNK = 1 << 20

_KIND_ERROR = {FileKind.AAB: ErrorCode.FILE_IS_AAB, FileKind.AIA: ErrorCode.FILE_IS_AIA,
               FileKind.AIX: ErrorCode.FILE_IS_AIX, FileKind.NOT_ANDROID: ErrorCode.FILE_NOT_ANDROID}


def classify(path: Path) -> FileKind:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return FileKind.NOT_ANDROID
    if "AndroidManifest.xml" in names:
        return FileKind.APK
    if "BundleConfig.pb" in names or any(n.startswith("base/manifest/") for n in names):
        return FileKind.AAB
    if "youngandroidproject/project.properties" in names:
        return FileKind.AIA
    if any(n.endswith(("components.json", "component.json", "component_build_infos.json")) for n in names):
        return FileKind.AIX
    return FileKind.NOT_ANDROID


def kind_error(kind: FileKind) -> ErrorCode | None:
    return _KIND_ERROR.get(kind)


def _copy_hashing(src: Path, tmp: Path, out: dict[str, object]) -> None:
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while chunk := fin.read(CHUNK):
                h.update(chunk)
                fout.write(chunk)
        out["sha"] = h.hexdigest()
    except OSError as e:
        out["error"] = e


def stage(path: Path, staging: Path, *, keep: int = 20, timeout_s: float = DRIVE_TIMEOUT_S) -> tuple[str, Path]:
    """Copy while hashing into staging/<sha256>.apk. Raises OrcusFailure (STAGE_*)."""
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f".incoming-{threading.get_ident()}.part"
    out: dict[str, object] = {}
    t = threading.Thread(target=_copy_hashing, args=(path, tmp, out), name="apk-stage", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_DRIVE_TIMEOUT, str(path)))
    if "error" in out or "sha" not in out:
        tmp.unlink(missing_ok=True)
        raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_COPY_FAILED, f"{path}: {out.get('error')}"))
    sha = str(out["sha"])
    staged = staging / f"{sha}.apk"
    if staged.exists():
        tmp.unlink(missing_ok=True)
    else:
        shutil.move(str(tmp), staged)
    staged.touch()   # LRU by modification time
    prune(staging, keep)
    return sha, staged


def prune(staging: Path, keep: int) -> None:
    files = sorted(staging.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
