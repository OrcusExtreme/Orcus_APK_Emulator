"""Shared paths and environment for the developer tools (bootstrap, lock, dev).

Everything heavy goes to D:\\OrcusDev so the Drive-synced source folder and C: stay small
(ARCHITECTURE.md §11.7). The tools are plain Python on purpose: cmd cannot cd into the Korean
project path and PowerShell 5.1 scripts need a BOM.
"""

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEV_ROOT = Path(os.environ.get("ORCUS_DEV_ROOT", r"D:\OrcusDev"))
VENV = DEV_ROOT / "venv"
VENV_PY = VENV / "Scripts" / "python.exe"
SITE_PACKAGES = VENV / "Lib" / "site-packages"
BASE_PY = Path(os.environ.get("LOCALAPPDATA", "")) / "Python" / "pythoncore-3.14-64" / "python.exe"

PIP_CACHE = DEV_ROOT / "pip-cache"
PYCACHE = DEV_ROOT / "pycache"
PYTEST_DIR = DEV_ROOT / "pytest"
PYRIGHT_CACHE = DEV_ROOT / "pyright"
NPM_CACHE = DEV_ROOT / "npm-cache"
PYINSTALLER_DIR = DEV_ROOT / "pyinstaller"
TMP = DEV_ROOT / "tmp"


def dev_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for every tool we start: caches on D:, UTF-8 mode, no pip self-check."""
    env = dict(os.environ if base is None else base)
    env.update({
        "PIP_CACHE_DIR": str(PIP_CACHE),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONPYCACHEPREFIX": str(PYCACHE),
        "PYTHONUTF8": "1",
        "PYRIGHT_PYTHON_CACHE_DIR": str(PYRIGHT_CACHE),
        "NPM_CONFIG_CACHE": str(NPM_CACHE),
        "PYINSTALLER_CONFIG_DIR": str(PYINSTALLER_DIR),
        "TMP": str(TMP),
        "TEMP": str(TMP),
    })
    return env


def ensure_dirs() -> None:
    for d in (DEV_ROOT, PIP_CACHE, PYCACHE, PYTEST_DIR, PYRIGHT_CACHE, NPM_CACHE, PYINSTALLER_DIR, TMP):
        d.mkdir(parents=True, exist_ok=True)


def run(argv: Sequence[str | os.PathLike[str]], *, cwd: Path | None = None) -> int:
    print("$", " ".join(str(a) for a in argv), flush=True)
    return subprocess.call([str(a) for a in argv], env=dev_env(), cwd=cwd or REPO)


def require_venv() -> Path:
    if not VENV_PY.exists():
        sys.exit(f"venv not found at {VENV}. Run: py -3.14 tools/bootstrap.py")
    return VENV_PY
