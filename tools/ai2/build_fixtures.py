"""Generate the App Inventor fixtures of docs/fixtures.md and, with --build, compile their APKs locally.

    py tools/ai2/build_fixtures.py                    write .aia + build zips, run the static checks
    py tools/ai2/build_fixtures.py --build            also build the APKs (buildserver first:
                                                      py tools/ai2/setup_toolchain.py, py tools/ai2/ant.py buildserver)
    py tools/ai2/build_fixtures.py --build OrcusProbe only that project

Outputs (nothing large goes into the repository):
    D:\\OrcusDev\\fixtures\\<Name>.apk               built APK
    D:\\OrcusDev\\fixtures\\src\\<Name>.aia           for the IDE: Projects > Import project (.aia)
    D:\\OrcusDev\\fixtures\\src\\<Name>.build.zip     buildserver input (.aia content + .yail + keystore)
    D:\\OrcusDev\\fixtures\\src\\android.keystore     one signing key for all fixtures, made by the first build
    tests\\fixtures\\OrcusProbe.aia                   the copy kept in the repository
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import devenv  # noqa: E402
from ai2 import aia, ant, projects  # noqa: E402

FIXTURES = devenv.DEV_ROOT / "fixtures"
SRC = FIXTURES / "src"
KEYSTORE = SRC / aia.KEYSTORE
RUN_LIB = ant.AI2 / "buildserver" / "build" / "run" / "lib"
REPO_COPIES = {"OrcusProbe": devenv.REPO / "tests" / "fixtures" / "OrcusProbe.aia"}
BUILD_TIMEOUT_S = 15 * 60


def generate(p: aia.Project) -> tuple[Path, Path]:
    problems = aia.problems(p)
    if problems:
        sys.exit(f"{p.name}: " + "; ".join(problems))
    aia_path = p.write_zip(SRC / f"{p.name}.aia", with_yail=False)
    zip_path = p.write_zip(SRC / f"{p.name}.build.zip", with_yail=True,
                           keystore=KEYSTORE if KEYSTORE.exists() else None)
    unpacked = SRC / p.name
    devenv.remove_tree(unpacked)
    for name, data in p.files(with_yail=True).items():   # readable copy for review and diffing
        (unpacked / name).parent.mkdir(parents=True, exist_ok=True)
        (unpacked / name).write_bytes(data)
    if p.name in REPO_COPIES:
        REPO_COPIES[p.name].parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(aia_path, REPO_COPIES[p.name])
    print(f"{p.name}: {aia_path.name}, {zip_path.name}")
    return aia_path, zip_path


def build(p: aia.Project) -> Path:
    """buildserver Main (the same call as `ant RunMain`, without re-running Ant every time)."""
    if not (RUN_LIB / "BuildServer.jar").exists():
        sys.exit(f"no buildserver at {RUN_LIB}: run `py tools/ai2/ant.py buildserver` once")
    # Rewritten here: the keystore may have appeared with the previous project's build.
    zip_path = p.write_zip(SRC / f"{p.name}.build.zip", with_yail=True,
                           keystore=KEYSTORE if KEYSTORE.exists() else None)
    work = devenv.TMP / "ai2build"
    out, java_tmp = work / p.name, work / "javatmp"
    devenv.remove_tree(out)
    out.mkdir(parents=True)
    java_tmp.mkdir(parents=True, exist_ok=True)
    cmd = [str(ant.JDK / "bin" / "java.exe"), "-Dfile.encoding=UTF-8", f"-Duser.home={ant.HOME}",
           f"-Djava.io.tmpdir={java_tmp}", "-Djava.awt.headless=true", "-Dkeystore.pkcs12.legacy=true",
           "-cp", str(RUN_LIB / "*"), "com.google.appinventor.buildserver.Main",
           "--inputZipFile", str(zip_path), "--userName", "orcus", "--outputDir", str(out),
           "--dexCacheDir", str(ant.HOME / "dexCache")]
    log = work / f"{p.name}.log"
    print(f"{p.name}: building (log {log})", flush=True)
    with open(log, "wb") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, env=ant.env(), timeout=BUILD_TIMEOUT_S)
    apk = out / f"{p.name}.apk"
    if rc != 0 or not apk.exists():
        sys.exit(f"{p.name}: build failed (exit {rc}), see {log}")
    if (out / aia.KEYSTORE).exists() and not KEYSTORE.exists():
        shutil.copyfile(out / aia.KEYSTORE, KEYSTORE)               # reuse it, so rebuilds install as updates
    target = FIXTURES / apk.name
    shutil.copyfile(apk, target)
    print(f"{p.name}: {target} ({target.stat().st_size // 1024} KiB)")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", metavar="NAME", help=f"any of {', '.join(projects.ALL)} (default: all)")
    ap.add_argument("--build", action="store_true", help="compile APKs with the local buildserver")
    args = ap.parse_args()
    if unknown := set(args.names) - projects.ALL.keys():
        ap.error(f"unknown project(s): {', '.join(sorted(unknown))}")
    devenv.ensure_dirs()
    for name in args.names or list(projects.ALL):
        p = projects.ALL[name]()
        generate(p)
        if args.build:
            build(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
