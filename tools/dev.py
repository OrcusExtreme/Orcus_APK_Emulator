"""Developer entry point. Runs everything inside D:\\OrcusDev\\venv with caches on D:.

    py tools/dev.py test [pytest args]     unit tests (device/network tests are opt-in: -m device)
    py tools/dev.py typecheck [args]       pyright
    py tools/dev.py check                  test + typecheck
    py tools/dev.py cli [orcus-cli args]   e.g. `cli doctor`
    py tools/dev.py lock                   regenerate the hash locks
    py tools/dev.py genprotos [dir]        vendor emulator protos + generate stubs
    py tools/dev.py build [--boot-check]   packaging smoke: PyInstaller onedir + packaged self-checks
"""

import sys

sys.dont_write_bytecode = True  # no tools/__pycache__ in the Drive folder
import devenv  # noqa: E402


def test(args: list[str]) -> int:
    py = devenv.require_venv()
    return devenv.run([py, "-m", "pytest", "-o", f"cache_dir={devenv.PYTEST_DIR / 'cache'}",
                       f"--basetemp={devenv.PYTEST_DIR / 'tmp'}", *args])


def typecheck(args: list[str]) -> int:
    py = devenv.require_venv()
    return devenv.run([py, "-m", "pyright", *args])


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, args = sys.argv[1], sys.argv[2:]
    devenv.ensure_dirs()
    match cmd:
        case "test":
            return test(args)
        case "typecheck":
            return typecheck(args)
        case "check":
            return test(args) or typecheck([])
        case "cli":
            return devenv.run([devenv.require_venv(), "-X", "utf8", "-m", "orcus.cli", *args])
        case "lock":
            return devenv.run([devenv.BASE_PY, devenv.REPO / "tools" / "lock.py"])
        case "genprotos":
            return devenv.run([devenv.require_venv(), devenv.REPO / "tools" / "gen_protos.py", *args])
        case "build":
            return devenv.run([devenv.BASE_PY, devenv.REPO / "tools" / "build.py", *args])
        case _:
            print(__doc__)
            return 2


if __name__ == "__main__":
    sys.exit(main())
