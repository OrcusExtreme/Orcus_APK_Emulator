"""Create the developer venv in D:\\OrcusDev\\venv and install the hash-locked dependencies.

Steps (all idempotent):
1. Check the install-manager CPython 3.14 (not the Store build).
2. Point pip, pycache, pyright and npm caches at D:\\OrcusDev before pip ever runs.
3. Create the venv, then pip install --require-hashes -r requirements-dev.lock.
4. Make `orcus` importable from the venv without building a wheel: a .pth file with the src path,
   plus a sitecustomize.py that keeps bytecode out of the Drive folder (sys.pycache_prefix).

Usage: py -3.14 tools/bootstrap.py
"""

import subprocess
import sys

sys.dont_write_bytecode = True  # no tools/__pycache__ in the Drive folder
import devenv  # noqa: E402

SITECUSTOMIZE = f'''"""Written by Orcus tools/bootstrap.py: keep __pycache__ out of the Drive-synced source folder."""
import sys

if sys.pycache_prefix is None:
    sys.pycache_prefix = {str(devenv.PYCACHE)!r}
'''


def main() -> None:
    if not devenv.BASE_PY.exists():
        sys.exit(f"CPython 3.14 (install manager) not found at {devenv.BASE_PY}")
    version = subprocess.run([str(devenv.BASE_PY), "-c", "import sys; print(sys.version_info[:2])"],
                             capture_output=True, text=True, check=True).stdout.strip()
    if version != "(3, 14)":
        sys.exit(f"{devenv.BASE_PY} is {version}, expected 3.14")

    devenv.ensure_dirs()
    if not devenv.VENV_PY.exists():
        if devenv.run([devenv.BASE_PY, "-m", "venv", devenv.VENV]) != 0:
            sys.exit("venv creation failed")

    code = devenv.run([devenv.VENV_PY, "-m", "pip", "install", "--require-hashes", "--only-binary=:all:",
                       "-r", devenv.REPO / "requirements-dev.lock"])
    if code != 0:
        sys.exit("pip install failed")

    (devenv.SITE_PACKAGES / "orcus-src.pth").write_text(str(devenv.REPO / "src") + "\n", encoding="utf-8")
    (devenv.SITE_PACKAGES / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    print(f"\nready: {devenv.VENV_PY}")
    print("next:  py tools/dev.py check")


if __name__ == "__main__":
    main()
