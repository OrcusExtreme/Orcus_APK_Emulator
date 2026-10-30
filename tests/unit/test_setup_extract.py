import dataclasses
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


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    for evil in ("../evil.txt", "/abs.txt", "C:/x.txt", "a/../../b.txt"):
        z = make_zip(tmp_path / "evil.zip", {"ok.txt": b"1", evil: b"2"})
        with pytest.raises(OrcusFailure) as e:
            extract.safe_extract(z, tmp_path / "out")
        assert e.value.error.code is ErrorCode.SETUP_CHECKSUM
        assert not (tmp_path / "evil.txt").exists()


def test_install_and_verify(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    z = make_zip(tmp_path / "fake.zip", GOOD)
    result = extract.install_archive(p, FAKE, z)
    assert result.ok and result.optional_missing == ("netsimd.exe",)
    assert result.found["*.proto"].endswith("emulator_controller.proto")
    assert (p.sdk / "fake" / "tools" / "adb.exe").exists()
    assert not (p.tmp / "extract_fake").exists()
    assert extract.verify_installed(p, FAKE).ok
    again = extract.install_archive(p, FAKE, z)   # replaces an existing install
    assert again.ok


def test_missing_required_file_reports_quarantine(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    entries = {k: v for k, v in GOOD.items() if not k.endswith("adb.exe")}
    with pytest.raises(OrcusFailure) as e:
        extract.install_archive(p, FAKE, make_zip(tmp_path / "fake.zip", entries))
    assert e.value.error.code is ErrorCode.SETUP_QUARANTINE and "adb.exe" in e.value.error.detail


def test_wrong_revision(tmp_path: Path) -> None:
    p = OrcusPaths(tmp_path)
    entries = GOOD | {"tools/source.properties": b"Pkg.Revision=36.0.0\n"}
    with pytest.raises(OrcusFailure) as e:
        extract.install_archive(p, FAKE, make_zip(tmp_path / "fake.zip", entries))
    assert e.value.error.code is ErrorCode.SETUP_CHECKSUM


def test_missing_top_folder(tmp_path: Path) -> None:
    with pytest.raises(OrcusFailure):
        extract.install_archive(OrcusPaths(tmp_path), FAKE, make_zip(tmp_path / "f.zip", {"other/a": b"1"}))


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
