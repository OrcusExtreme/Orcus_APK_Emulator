"""Runtime folder layout (ARCHITECTURE.md §11.1) and the root guards (§11.4)."""

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from . import win32
from .pins import Archive

AVD_NAME = "orcus36"
GOLDEN_SNAPSHOT = "orcus_golden"
DEFAULT_HOME = Path(r"D:\OrcusAPK")
HOME_FILE_NAME = "orcus_home.txt"
MAX_HOME_LEN = 40
DRIVE_MARKERS = (".tmp.driveupload", ".tmp.drivedownload")


@dataclass(frozen=True)
class OrcusPaths:
    root: Path

    @property
    def sdk(self) -> Path:
        return self.root / "sdk"

    @property
    def emulator_dir(self) -> Path:
        return self.sdk / "emulator"

    @property
    def emulator_exe(self) -> Path:
        return self.emulator_dir / "emulator.exe"

    @property
    def emulator_check_exe(self) -> Path:
        return self.emulator_dir / "emulator-check.exe"

    @property
    def platform_tools(self) -> Path:
        return self.sdk / "platform-tools"

    @property
    def adb_exe(self) -> Path:
        return self.platform_tools / "adb.exe"

    @property
    def platforms(self) -> Path:
        return self.sdk / "platforms"

    def package_dir(self, archive: Archive) -> Path:
        return self.sdk / archive.install_dir

    def partial_marker(self, archive: Archive) -> Path:
        """`<package>.partial`: setup started extracting this package and has not finished (setup/extract.py)."""
        target = self.package_dir(archive)
        return target.with_name(target.name + ".partial")

    @property
    def avd_dir(self) -> Path:
        return self.root / "avd"

    @property
    def avd_ini(self) -> Path:
        return self.avd_dir / f"{AVD_NAME}.ini"

    @property
    def avd_data(self) -> Path:
        return self.avd_dir / f"{AVD_NAME}.avd"

    @property
    def golden_dir(self) -> Path:
        return self.avd_data / "snapshots" / GOLDEN_SNAPSHOT

    @property
    def android_home(self) -> Path:
        """ANDROID_EMULATOR_HOME = ANDROID_USER_HOME; holds the shared adb key."""
        return self.root / "home"

    @property
    def adbkey(self) -> Path:
        return self.android_home / "adbkey"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def app(self) -> Path:
        return self.root / "app"

    @property
    def camera_image(self) -> Path:
        """[P4] What the phone's back camera shows (engine.camera)."""
        return self.state / "camera.png"

    def state_file(self, name: str) -> Path:
        return self.state / name

    def ensure(self) -> None:
        for d in (self.root, self.sdk, self.avd_dir, self.android_home, self.tmp, self.downloads,
                  self.staging, self.logs, self.state):
            d.mkdir(parents=True, exist_ok=True)


def resolve_home(cli_home: str | None, env: Mapping[str, str], exe_dir: Path | None) -> Path:
    """--home > ORCUS_HOME > <exe folder>\\orcus_home.txt > D:\\OrcusAPK."""
    if cli_home:
        return Path(cli_home)
    if env.get("ORCUS_HOME"):
        return Path(env["ORCUS_HOME"])
    if exe_dir is not None:
        marker = exe_dir / HOME_FILE_NAME
        try:
            text = marker.read_text(encoding="utf-8-sig").strip()
        except OSError:
            text = ""
        if text:
            return Path(text)
    return DEFAULT_HOME


def home_problems(root: Path, *,
                  volume: Callable[[str], tuple[str, str] | None] = win32.volume_info,
                  env: Mapping[str, str] = os.environ) -> list[str]:
    """Reasons (message keys) why `root` cannot hold the SDK and AVD. Empty means OK."""
    problems: list[str] = []
    text = str(root)
    if not root.is_absolute() or not root.drive:
        return ["path.not_absolute"]
    if not text.isascii():
        problems.append("path.non_ascii")   # 37.2.12 hangs on non-ASCII AVD paths
    if len(text) > MAX_HOME_LEN:
        problems.append("path.too_long")
    info = volume(root.anchor)
    if info is None:
        problems.append("path.no_drive")
    else:
        label, fs_name = info
        if label.strip().lower() == "google drive":
            problems.append("path.drive_virtual")
        elif fs_name.upper() != "NTFS":
            problems.append("path.not_ntfs")
    if any((p / m).exists() for p in (root, *root.parents) for m in DRIVE_MARKERS):
        problems.append("path.drive_sync")
    local = env.get("LOCALAPPDATA")
    lowered = text.lower()
    if "windowsapps" in (part.lower() for part in root.parts) or (
            local and lowered.startswith(str(Path(local) / "Packages").lower())):
        problems.append("path.store_sandbox")
    return problems


def interpreter_problems(executable: str = sys.executable,
                         version: tuple[int, int] = (sys.version_info.major, sys.version_info.minor)) -> list[str]:
    problems: list[str] = []
    if "windowsapps" in executable.lower():
        problems.append("python.store")
    if version != (3, 14):
        problems.append("python.version")
    return problems
