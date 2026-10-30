"""Safe extraction into tmp\\extract_*, move into sdk\\, then check the key files survived V3."""

import fnmatch
import os
import shutil
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from orcus.common import messages_ko
from orcus.common.paths import OrcusPaths
from orcus.common.pins import EMULATOR_BUILD, Archive, revision_tuple
from orcus.contracts import ErrorCode, OrcusFailure

type Progress = Callable[[int, int], None]


class Cancelled(Exception):
    pass


def unsafe_members(zf: zipfile.ZipFile) -> list[str]:
    """Entries that would escape the target folder (zip-slip): absolute, drive, or '..' parts."""
    bad: list[str] = []
    for name in zf.namelist():
        p = PurePosixPath(name.replace("\\", "/"))
        if p.is_absolute() or ":" in name or any(part == ".." for part in p.parts):
            bad.append(name)
    return bad


def safe_extract(zip_path: Path, dest: Path, *, progress: Progress | None = None,
                 cancel: threading.Event | None = None) -> None:
    """Extract everything or nothing: an unsafe entry rejects the whole archive."""
    with zipfile.ZipFile(zip_path) as zf:
        bad = unsafe_members(zf)
        if bad:
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"unsafe zip entries: {bad[:5]}"))
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        done = 0
        root = dest.resolve()
        for info in infos:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            target = (root / info.filename).resolve()
            if target != root and root not in target.parents:
                raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"unsafe zip entry: {info.filename}"))
            zf.extract(info, root)
            done += info.file_size
            if progress is not None:
                progress(done, total)


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


@dataclass(frozen=True)
class Verification:
    missing: tuple[str, ...]
    optional_missing: tuple[str, ...]
    found: dict[str, str]   # required name -> first matching relative path
    revision_ok: bool

    @property
    def ok(self) -> bool:
        return not self.missing and self.revision_ok


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
    return Verification(missing, optional_missing, names, revision_ok)


def _remove_tree(path: Path) -> None:
    def onexc(func: Callable[..., object], p: str, exc: BaseException) -> None:
        os.chmod(p, 0o666)
        func(p)
    shutil.rmtree(path, onexc=onexc)


def install_archive(paths: OrcusPaths, archive: Archive, zip_path: Path, *, progress: Progress | None = None,
                    cancel: threading.Event | None = None) -> Verification:
    """Extract to tmp\\extract_<key>, move <zip_top> into place, verify. Raises SETUP_QUARANTINE."""
    work = paths.tmp / f"extract_{archive.key}"
    if work.exists():
        _remove_tree(work)
    work.mkdir(parents=True)
    safe_extract(zip_path, work, progress=progress, cancel=cancel)
    top = work / archive.zip_top
    if not top.is_dir():
        raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{zip_path.name}: no {archive.zip_top}/ inside"))
    target = paths.package_dir(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        trash = paths.tmp / f"old_{archive.key}_{int(time.time())}"
        target.rename(trash)
        _remove_tree(trash)
    top.rename(target)   # same volume: a directory rename
    _remove_tree(work)
    time.sleep(0.5)   # give an on-access scanner a moment before we look
    result = verify_installed(paths, archive)
    if result.missing:
        detail = messages_ko.text("setup.quarantine.missing", files=", ".join(result.missing))
        raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_QUARANTINE, f"{archive.key}: {detail}"))
    if not result.revision_ok:
        raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{archive.key}: unexpected source.properties"))
    return result
