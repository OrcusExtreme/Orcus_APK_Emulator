from pathlib import Path

from orcus.common import paths
from orcus.common.paths import OrcusPaths, home_problems, interpreter_problems, resolve_home

NTFS = lambda root: ("Data", "NTFS")  # noqa: E731


def test_resolve_order(tmp_path: Path) -> None:
    (tmp_path / paths.HOME_FILE_NAME).write_text("E:\\FromFile\n", encoding="utf-8-sig")
    env = {"ORCUS_HOME": "F:\\FromEnv"}
    assert resolve_home("G:\\Cli", env, tmp_path) == Path("G:\\Cli")
    assert resolve_home(None, env, tmp_path) == Path("F:\\FromEnv")
    assert resolve_home(None, {}, tmp_path) == Path("E:\\FromFile")
    assert resolve_home(None, {}, None) == paths.DEFAULT_HOME
    assert resolve_home(None, {}, tmp_path / "missing") == paths.DEFAULT_HOME


def test_default_home_passes() -> None:
    assert home_problems(Path(r"D:\OrcusAPK"), volume=NTFS, env={}) == []


def test_guards_reject_each_rule() -> None:
    assert "path.non_ascii" in home_problems(Path(r"D:\오르카"), volume=NTFS, env={})
    assert "path.too_long" in home_problems(Path("D:\\" + "a" * 40), volume=NTFS, env={})
    assert "path.not_ntfs" in home_problems(Path(r"E:\Orcus"), volume=lambda r: ("USB", "FAT32"), env={})
    assert "path.drive_virtual" in home_problems(Path(r"G:\Orcus"), volume=lambda r: ("Google Drive", "FAT32"), env={})
    assert "path.no_drive" in home_problems(Path(r"Q:\Orcus"), volume=lambda r: None, env={})
    assert home_problems(Path("relative"), volume=NTFS, env={}) == ["path.not_absolute"]
    local = r"C:\Users\x\AppData\Local"
    assert "path.store_sandbox" in home_problems(Path(local + r"\Packages\a"), volume=NTFS, env={"LOCALAPPDATA": local})
    assert "path.store_sandbox" in home_problems(Path(r"C:\Program Files\WindowsApps\x"), volume=NTFS, env={})


def test_drive_sync_ancestor(tmp_path: Path) -> None:
    (tmp_path / ".tmp.driveupload").mkdir()
    inner = tmp_path / "a" / "b"
    assert "path.drive_sync" in home_problems(inner, volume=NTFS, env={})


def test_project_folder_is_drive_synced() -> None:
    """This source folder sits in a Drive-synced tree: it must never be chosen as ORCUS_HOME."""
    repo = Path(__file__).resolve().parents[2]
    problems = home_problems(repo)
    assert "path.non_ascii" in problems


def test_interpreter_rules() -> None:
    store = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_q\python.exe"
    assert interpreter_problems(store, (3, 13)) == ["python.store", "python.version"]
    assert interpreter_problems(r"C:\Python\python.exe", (3, 14)) == []


def test_layout() -> None:
    p = OrcusPaths(Path(r"D:\OrcusAPK"))
    assert p.adb_exe == Path(r"D:\OrcusAPK\sdk\platform-tools\adb.exe")
    assert p.avd_data == Path(r"D:\OrcusAPK\avd\orcus36.avd")
    assert p.golden_dir == Path(r"D:\OrcusAPK\avd\orcus36.avd\snapshots\orcus_golden")
    assert p.adbkey == Path(r"D:\OrcusAPK\home\adbkey")
    assert p.state_file("engine.lock") == Path(r"D:\OrcusAPK\state\engine.lock")
