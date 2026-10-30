"""Run Ant for appinventor-sources with the D:\\OrcusDev JDK 17 and nothing written under C:\\Users.

Usage: py tools/ai2/ant.py <dir relative to ai2src\\appinventor> [ant args...]
  e.g. py tools/ai2/ant.py buildserver
"""

import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import devenv  # noqa: E402

JDK = devenv.DEV_ROOT / "tools" / "jdk17"
ANT = devenv.DEV_ROOT / "tools" / "ant"
AI2 = devenv.DEV_ROOT / "ai2src" / "appinventor"
HOME = devenv.DEV_ROOT / "ai2home"   # user.home for Ant/Java caches (instead of C:\Users\...)


def env() -> dict[str, str]:
    e = devenv.dev_env()
    HOME.mkdir(parents=True, exist_ok=True)
    e.update({
        "JAVA_HOME": str(JDK),
        "ANT_HOME": str(ANT),
        "ANT_OPTS": f"-Duser.home={HOME} -Dfile.encoding=UTF-8 -Xmx2g",
        "PATH": f"{JDK / 'bin'};{ANT / 'bin'};{e.get('PATH', '')}",
    })
    return e


def run(subdir: str, *args: str) -> int:
    cwd = AI2 / subdir
    print("$ ant", *args, "  (in", cwd, ")", flush=True)
    return subprocess.call(["cmd", "/c", str(ANT / "bin" / "ant.bat"), *args], cwd=cwd, env=env())


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "buildserver", *sys.argv[2:]))
