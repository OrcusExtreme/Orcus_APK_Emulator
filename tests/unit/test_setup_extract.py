import dataclasses
import threading
import time
import zipfile
from pathlib import Path

import pytest

from orcus.common import pins
from orcus.common.paths import OrcusPaths
from orcus.contracts import ErrorCode, OrcusFailure
from orcus.setup import extract

FAKE = dataclasses.replace(
    pins.PLATFORM_TOOLS, key="fake", install_dir=r"fake\tools", zip_top="tools",
    required=("source.properties", "adb.exe", "*.proto"), optional=("netsimd.exe",))


def make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


GOOD = {
    "tools/source.properties": b"Pkg.Revision=37.0.1\nPkg.Desc=fake\n",
    "tools/adb.exe": b"MZ",
    "tools/lib/emulator_controller.proto": b"syntax = 'proto3';",
}


@pytest.fixture(autouse=True)
def no_settle_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "SETTLE_S", 0.0)


def test_zip_slip_and_windows_invalid_names_are_rejected(tmp_path: Path) -> None:
    for evil in ("tools/../../evil.txt", "/abs.txt", "C:/x.txt", "tools/a|b.txt"):
        z = make_zip(tmp_path / "evil.zip", {"tools/ok.txt": b"1", evil: b"2"})
        with pytest.raises(OrcusFailure) as e:
            extract.extract_into(z, "tools", tmp_path / "out")
        assert e.value.error.code is ErrorCode.SETUP_CHECKSUM
        assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "out" / "ok.txt").exists()


def test_install_writes_straight_into_place(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    z = make_zip(tmp_path / "fake.zip", GOOD | {"other/ignored.txt": b"x"})
    assert extract.uncompressed_size(z, "tools") == sum(len(v) for v in GOOD.values())
    result = extract.install_archive(p, FAKE, z)
    assert result.ok and result.optional_missing == ("netsimd.exe",)
    assert result.found["*.proto"].endswith("emulator_controller.proto")
    assert (p.sdk / "fake" / "tools" / "adb.exe").read_bytes() == b"MZ"
    assert not (p.sdk / "fake" / "tools" / "ignored.txt").exists()
    assert not extract.partial_marker(p, FAKE).exists()
    assert not p.tmp.exists() or not any(p.tmp.iterdir()), "no temporary extract folder any more"
    assert extract.install_archive(p, FAKE, z).ok   # replacing an existing install works


def test_partial_marker_means_not_installed(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    z = make_zip(tmp_path / "fake.zip", GOOD)
    extract.install_archive(p, FAKE, z)
    extract.partial_marker(p, FAKE).write_text("interrupted")
    v = extract.verify_installed(p, FAKE)
    assert v.partial and not v.ok and not v.missing
    assert extract.install_archive(p, FAKE, z).ok and not extract.partial_marker(p, FAKE).exists()


def test_cancelled_extraction_stays_partial(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    z = make_zip(tmp_path / "fake.zip", GOOD)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(extract.Cancelled):
        extract.install_archive(p, FAKE, z, cancel=cancel)
    assert extract.partial_marker(p, FAKE).exists()
    assert not extract.verify_installed(p, FAKE).ok


def test_missing_required_file_reports_quarantine(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    entries = {k: v for k, v in GOOD.items() if not k.endswith("adb.exe")}
    with pytest.raises(OrcusFailure) as e:
        extract.install_archive(p, FAKE, make_zip(tmp_path / "fake.zip", entries))
    assert e.value.error.code is ErrorCode.SETUP_QUARANTINE and "adb.exe" in e.value.error.detail
    assert e.value.error.message_key == "error.setup_quarantine"
    assert extract.partial_marker(p, FAKE).exists()


def test_wrong_revision(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    entries = GOOD | {"tools/source.properties": b"Pkg.Revision=36.0.0\n"}
    with pytest.raises(OrcusFailure) as e:
        extract.install_archive(p, FAKE, make_zip(tmp_path / "fake.zip", entries))
    assert e.value.error.code is ErrorCode.SETUP_CHECKSUM


def test_missing_top_folder(tmp_path: Path) -> None:
    with pytest.raises(OrcusFailure):
        extract.install_archive(OrcusPaths(tmp_path), FAKE, make_zip(tmp_path / "f.zip", {"other/a": b"1"}))


def hold_open(path: Path, seconds: float) -> threading.Thread:
    """Python opens files without FILE_SHARE_DELETE, like a scanner holding a fresh file."""
    f = open(path, "rb")
    t = threading.Thread(target=lambda: (time.sleep(seconds), f.close()), daemon=True)
    t.start()
    return t


def test_remove_tree_waits_for_a_scanner_lock(tmp_path: Path) -> None:
    d = tmp_path / "pkg"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "file.dll").write_bytes(b"MZ")
    t = hold_open(d / "sub" / "file.dll", 0.4)
    start = time.monotonic()
    extract.remove_tree(d, timeout_s=10)
    assert not d.exists() and time.monotonic() - start >= 0.3
    t.join()


def test_remove_tree_gives_up_with_the_locked_message(tmp_path: Path) -> None:
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "file.dll").write_bytes(b"MZ")
    t = hold_open(d / "file.dll", 1.0)
    with pytest.raises(OrcusFailure) as e:
        extract.remove_tree(d, timeout_s=0.3)
    assert e.value.error.code is ErrorCode.SETUP_QUARANTINE
    assert e.value.error.message_key == "setup.extract.locked"
    t.join()


def test_retry_locked_passes_other_errors_through() -> None:
    def boom() -> None:
        raise FileNotFoundError("gone")
    with pytest.raises(FileNotFoundError):
        extract.retry_locked(boom, Path("x"), timeout_s=5)


def test_emulator_build_id_must_match(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    base = p.package_dir(pins.EMULATOR)
    base.mkdir(parents=True)
    for name in ("emulator.exe", "emulator-check.exe", "qemu-system-x86_64-headless.exe", "qemu-system-x86_64.exe",
                 "emulator_access.json", "a.proto"):
        (base / name).write_bytes(b"x")
    (base / "source.properties").write_text("Pkg.Revision=37.2.12\nPkg.BuildId=16428233\n")
    assert extract.verify_installed(p, pins.EMULATOR).ok
    (base / "source.properties").write_text("Pkg.Revision=37.2.12\nPkg.BuildId=16740729\n")
    assert not extract.verify_installed(p, pins.EMULATOR).ok
