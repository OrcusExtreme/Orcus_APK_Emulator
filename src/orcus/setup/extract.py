"""Extract SDK zips straight into sdk\\, then check the key files survived the virus scanners.

No folder is renamed after extraction. On this PC Windows Defender and AhnLab V3 both scan new
executables on access and briefly hold them open, which makes renaming or deleting their folder
fail with "access denied" (setup's first run failed that way on 2026-10-30, see docs/spikes.md S0b).
Instead a `<package>.partial` file next to the package folder marks an unfinished extraction; it is
removed only after verification, so an interrupted install is never mistaken for a complete one.
Deleting old trees is retried while a scanner holds files.
"""

import fnmatch
import os
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from orcus.common import log, messages_ko
from orcus.common.paths import OrcusPaths
from orcus.common.pins import EMULATOR_BUILD, Archive, revision_tuple
from orcus.contracts import ErrorCode, OrcusFailure

type Progress = Callable[[int, int], None]

LOCK_TIMEOUT_S = 120.0
SETTLE_S = 0.5   # let an on-access scanner act on new files before we verify them
CHUNK = 8 << 20
_LOCK_WINERRORS = {5, 32, 33, 145}   # access denied, sharing/lock violation, directory not empty (delete pending)
_INVALID_CHARS = set('<>:"|?*')
_log = log.get("setup.extract")


class Cancelled(Exception):
    pass


def _is_lock_error(e: OSError) -> bool:
    return isinstance(e, PermissionError) or getattr(e, "winerror", None) in _LOCK_WINERRORS


def _locked(path: Path, e: OSError) -> OrcusFailure:
    return OrcusFailure(messages_ko.error(ErrorCode.SETUP_QUARANTINE, f"{path}: {e}",
                                          message_key="setup.extract.locked"))


def retry_locked[T](fn: Callable[[], T], path: Path, *, timeout_s: float = LOCK_TIMEOUT_S,
                    sleep: Callable[[float], None] = time.sleep) -> T:
    """Run `fn`, retrying while a scanner holds files open. Raises SETUP_QUARANTINE (locked) at the end."""
    deadline = time.monotonic() + timeout_s
    delay = 0.25
    while True:
        try:
            return fn()
        except OSError as e:
            if not _is_lock_error(e):
                raise
            if time.monotonic() >= deadline:
                raise _locked(path, e) from e
            _log.info("waiting for a file lock on %s: %s", path, e)
            sleep(delay)
            delay = min(delay * 2, 3.0)


def remove_tree(path: Path, *, timeout_s: float = LOCK_TIMEOUT_S) -> None:
    """shutil.rmtree that clears read-only flags and waits out scanner locks."""
    def onexc(func: Callable[..., object], p: str, exc: BaseException) -> None:
        if isinstance(exc, FileNotFoundError):
            return
        if isinstance(exc, PermissionError):
            try:
                os.chmod(p, 0o666)
                func(p)
                return
            except OSError:
                pass
        raise exc

    def attempt() -> None:
        if path.exists():
            shutil.rmtree(path, onexc=onexc)
    retry_locked(attempt, path, timeout_s=timeout_s)


def unsafe_members(zf: zipfile.ZipFile) -> list[str]:
    """Entries that would escape the target folder (zip-slip) or cannot exist on Windows."""
    bad: list[str] = []
    for name in zf.namelist():
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or any(part == ".." for part in p.parts) or any(c in _INVALID_CHARS for c in name):
            bad.append(name)
    return bad


def _members_under(zf: zipfile.ZipFile, top: str) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    out: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
    for info in zf.infolist():
        parts = PurePosixPath(info.filename.replace("\\", "/")).parts
        if len(parts) > 1 and parts[0] == top:
            out.append((info, PurePosixPath(*parts[1:])))
    return out


def uncompressed_size(zip_path: Path, top: str) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        return sum(info.file_size for info, _ in _members_under(zf, top))


def extract_into(zip_path: Path, top: str, dest: Path, *, progress: Progress | None = None,
                 cancel: threading.Event | None = None) -> int:
    """Write the entries under `top/` into `dest`. Every entry is validated before anything is written.

    Returns the number of files written. Entries outside `top/` are ignored.
    """
    with zipfile.ZipFile(zip_path) as zf:
        bad = unsafe_members(zf)
        if bad:
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"unsafe zip entries: {bad[:5]}"))
        members = _members_under(zf, top)
        if not members:
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{zip_path.name}: no {top}/ inside"))
        root = dest.resolve()
        total = sum(info.file_size for info, _ in members)
        done = files = 0
        for info, rel in members:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            target = (root / Path(*rel.parts)).resolve()
            if root not in target.parents:
                raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"unsafe zip entry: {info.filename}"))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while block := src.read(CHUNK):   # system.img alone is 4.2 GB: report inside big files
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    out.write(block)
                    done += len(block)
                    if progress is not None:
                        progress(done, total)
            files += 1
        return files


def read_properties(path: Path) -> dict[str, str]:
    props: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return props
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


def partial_marker(paths: OrcusPaths, archive: Archive) -> Path:
    target = paths.package_dir(archive)
    return target.with_name(target.name + ".partial")


@dataclass(frozen=True)
class Verification:
    missing: tuple[str, ...]
    optional_missing: tuple[str, ...]
    found: dict[str, str]   # required name -> first matching relative path
    revision_ok: bool
    partial: bool = False   # an extraction started and never finished

    @property
    def ok(self) -> bool:
        return not self.missing and self.revision_ok and not self.partial


def verify_installed(paths: OrcusPaths, archive: Archive) -> Verification:
    """Look for each required name anywhere under the package folder (exact layout is checked in S6)."""
    base = paths.package_dir(archive)
    names: dict[str, str] = {}
    if base.is_dir():
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                rel = os.path.relpath(os.path.join(dirpath, f), base)
                for pattern in (*archive.required, *archive.optional):
                    if pattern not in names and fnmatch.fnmatch(f.lower(), pattern.lower()):
                        names[pattern] = rel
    missing = tuple(p for p in archive.required if p not in names)
    optional_missing = tuple(p for p in archive.optional if p not in names)
    props = read_properties(base / "source.properties")
    revision_ok = revision_tuple(props.get("Pkg.Revision", "")) == revision_tuple(archive.revision)
    if archive.key == "emulator" and "Pkg.BuildId" in props:
        revision_ok = revision_ok and props["Pkg.BuildId"] == EMULATOR_BUILD
    return Verification(missing, optional_missing, names, revision_ok, partial_marker(paths, archive).exists())


def install_archive(paths: OrcusPaths, archive: Archive, zip_path: Path, *, progress: Progress | None = None,
                    cancel: threading.Event | None = None) -> Verification:
    """Extract straight into sdk\\<install_dir>, verify, then drop the .partial marker.

    Raises SETUP_QUARANTINE when required files vanished after extraction (a scanner removed them),
    or with the 'locked' message when a scanner kept an old tree from being deleted.
    """
    target = paths.package_dir(archive)
    marker = partial_marker(paths, archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"extracting {zip_path.name} since {time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    remove_tree(target)   # an older or unfinished copy
    target.mkdir()
    started = time.monotonic()
    files = extract_into(zip_path, archive.zip_top, target, progress=progress, cancel=cancel)
    _log.info("%s: %d files extracted in %.1f s", archive.key, files, time.monotonic() - started)
    time.sleep(SETTLE_S)
    result = verify_installed(paths, archive)
    if result.missing:
        detail = messages_ko.text("setup.quarantine.missing", files=", ".join(result.missing))
        raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_QUARANTINE, f"{archive.key}: {detail}"))
    if not result.revision_ok:
        raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{archive.key}: unexpected source.properties"))
    retry_locked(lambda: marker.unlink(missing_ok=True), marker)
    return verify_installed(paths, archive)
