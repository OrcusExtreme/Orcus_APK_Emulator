"""Intake (ARCHITECTURE.md §7.1–§7.2): what was dropped, by content; then an ASCII-named staged copy.

Unicode and Google Drive paths are read only through Python's Unicode file APIs; adb and the other tools only
ever see `<home>\\staging\\<sha256>.apk`. An "online only" Drive file starts downloading when it is read, so
the copy runs on a helper thread with a deadline.
"""

import hashlib
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from orcus.common import log, messages_ko
from orcus.contracts import ErrorCode, FileKind, OrcusFailure

_log = log.get("apk.intake")
DRIVE_TIMEOUT_S = 120.0
CHUNK = 1 << 20
MAX_ENTRIES = 50_000      # App Inventor APKs hold a few thousand; millions of tiny entries exhaust memory (3a)
STALE_PART_S = 3600.0     # a .part this old belongs to no copy any more

_KIND_ERROR = {FileKind.AAB: ErrorCode.FILE_IS_AAB, FileKind.AIA: ErrorCode.FILE_IS_AIA,
               FileKind.AIX: ErrorCode.FILE_IS_AIX, FileKind.NOT_ANDROID: ErrorCode.FILE_NOT_ANDROID}


def entry_count(path: Path) -> int | None:
    """Entries the zip's end record claims, read without loading the central directory; None: no zip end."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - (65_535 + 22)))   # the end record plus the longest comment
            tail = f.read()
    except OSError:
        return None
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or len(tail) - i < 22:
        return None
    count = int.from_bytes(tail[i + 10:i + 12], "little")
    if count == 0xFFFF:   # ZIP64: the real count is in the zip64 end record
        j = tail.rfind(b"PK\x06\x06", 0, i)
        if j >= 0 and i - j >= 40:
            count = int.from_bytes(tail[j + 32:j + 40], "little")
    return count


def classify(path: Path) -> FileKind:
    """Never raises: anything that is not a readable zip of a sane size is NOT_ANDROID (3a review)."""
    count = entry_count(path)
    if count is None or count > MAX_ENTRIES:
        return FileKind.NOT_ANDROID
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except Exception:
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


def _copy_hashing(src: Path, tmp: Path, out: dict[str, object], stop: threading.Event) -> None:
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            while chunk := fin.read(CHUNK):
                if stop.is_set():   # timed out: give up and leave nothing behind
                    break
                h.update(chunk)
                fout.write(chunk)
            else:
                out["sha"] = h.hexdigest()
    except OSError as e:
        out["error"] = e
    if stop.is_set():
        tmp.unlink(missing_ok=True)


def stage(path: Path, staging: Path, *, keep: int = 20, timeout_s: float = DRIVE_TIMEOUT_S,
          suffix: str = ".apk") -> tuple[str, Path]:
    """Copy while hashing into staging/<sha256><suffix> (a dropped picture keeps its extension, P4). Raises
    OrcusFailure (STAGE_*).

    Each call has its own temporary file: a copy abandoned at the Drive deadline keeps writing for a while, and a
    shared name let the next drop's file mix with it (3a review)."""
    staging.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=staging, prefix=".incoming-", suffix=".part")
    os.close(fd)
    tmp = Path(name)
    out: dict[str, object] = {}
    stop = threading.Event()
    t = threading.Thread(target=_copy_hashing, args=(path, tmp, out, stop), name="apk-stage", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        stop.set()   # the copy stops at its next chunk and removes its file
        raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_DRIVE_TIMEOUT, str(path)))
    if "error" in out or "sha" not in out:
        tmp.unlink(missing_ok=True)
        raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_COPY_FAILED, f"{path}: {out.get('error')}"))
    sha = str(out["sha"])
    staged = staging / f"{sha}{suffix}"
    try:
        os.replace(tmp, staged)   # same content by name; a half-written older copy is replaced too
        staged.touch()            # LRU by modification time
    except OSError as e:          # a scanner holds the old copy, say: a final stage, not a stuck run
        tmp.unlink(missing_ok=True)
        raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_COPY_FAILED, f"{path}: {e}")) from e
    prune(staging, keep, "*.apk" if suffix == ".apk" else "[0-9a-f]*.*")
    return sha, staged


def prune(staging: Path, keep: int, pattern: str = "*.apk") -> None:
    """Keep the `keep` newest staged files (at least the one just staged) and drop stale temporary files."""
    files = sorted(staging.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[max(keep, 1):]:
        try:
            old.unlink()
        except OSError:
            pass
    now = time.time()
    for part in staging.glob(".incoming-*.part"):
        try:
            if now - part.stat().st_mtime > STALE_PART_S:
                part.unlink()
        except OSError:
            pass
