"""Local App Inventor build toolchain for test fixtures (developer only, everything under D:\\OrcusDev).

App Inventor normally builds in the browser IDE behind a Google login. Its open-source buildserver
(mit-cml/appinventor-sources, Apache-2.0) can build an APK from a project zip on the command line, so the
fixtures (HelloPurr, TalkToMe, OrcusProbe) can be built here without anyone's account.

1. Temurin JDK 17 (zip, SHA-256 from the Adoptium API) -> D:\\OrcusDev\\tools\\jdk17
2. Apache Ant 1.10.15 (zip, SHA-512 from apache.org)   -> D:\\OrcusDev\\tools\\ant
3. appinventor-sources, shallow clone                   -> D:\\OrcusDev\\ai2src

Usage: py tools/ai2/setup_toolchain.py
"""

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import devenv  # noqa: E402

TOOLS = devenv.DEV_ROOT / "tools"
JDK_DIR = TOOLS / "jdk17"
ANT_DIR = TOOLS / "ant"
AI2_SRC = devenv.DEV_ROOT / "ai2src"
ADOPTIUM = "https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse"
ANT_ZIP = "https://archive.apache.org/dist/ant/binaries/apache-ant-1.10.15-bin.zip"
AI2_REPO = "https://github.com/mit-cml/appinventor-sources.git"


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "orcus-dev"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as out:
        shutil.copyfileobj(r, out, 1 << 20)
    return dest


def digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def unzip_single_root(archive: Path, target: Path) -> None:
    """Extract an archive whose content sits in one top folder, then move that folder to `target`."""
    work = archive.with_suffix(".extract")
    devenv.remove_tree(work)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(work)
    (top,) = list(work.iterdir())
    devenv.remove_tree(target)
    shutil.move(str(top), str(target))
    devenv.remove_tree(work)


def install_jdk() -> None:
    if (JDK_DIR / "bin" / "java.exe").exists():
        print("jdk17 present")
        return
    with urllib.request.urlopen(urllib.request.Request(ADOPTIUM, headers={"User-Agent": "orcus-dev"}), timeout=60) as r:
        info = json.load(r)[0]["binary"]["package"]
    archive = fetch(info["link"], devenv.TMP / info["name"])
    if digest(archive, "sha256") != info["checksum"]:
        sys.exit("JDK checksum mismatch")
    unzip_single_root(archive, JDK_DIR)
    archive.unlink()
    print("jdk17 ->", JDK_DIR)


def install_ant() -> None:
    if (ANT_DIR / "bin" / "ant.bat").exists():
        print("ant present")
        return
    archive = fetch(ANT_ZIP, devenv.TMP / "apache-ant-1.10.15-bin.zip")
    with urllib.request.urlopen(urllib.request.Request(ANT_ZIP + ".sha512", headers={"User-Agent": "orcus-dev"}),
                                timeout=60) as r:
        expected = r.read().decode().split()[0].lower()
    if digest(archive, "sha512") != expected:
        sys.exit("Ant checksum mismatch")
    unzip_single_root(archive, ANT_DIR)
    archive.unlink()
    print("ant ->", ANT_DIR)


def clone_ai2() -> None:
    if (AI2_SRC / "appinventor" / "buildserver").exists():
        print("appinventor-sources present:", subprocess.run(["git", "-C", str(AI2_SRC), "rev-parse", "HEAD"],
                                                             capture_output=True, text=True).stdout.strip())
        return
    subprocess.run(["git", "clone", "--depth", "1", "--single-branch", AI2_REPO, str(AI2_SRC)], check=True)
    print("appinventor-sources ->", AI2_SRC)


if __name__ == "__main__":
    devenv.ensure_dirs()
    install_jdk()
    install_ant()
    clone_ai2()
