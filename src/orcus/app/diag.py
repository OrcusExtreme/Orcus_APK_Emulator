"""Diagnostic bundle: one zip for whoever helps, with what Orcus knows.

In it: [Copy report]'s text, `orcus-cli doctor`'s lines, the newest Orcus and emulator logs, the adb server log,
the state files and the AVD's config.ini. Not in it: the SDK, the phone's disks and snapshots, the staged APKs and
pictures, the adb key. Every text is masked first: the App Inventor account in package names, the Windows user and
PC names, e-mail addresses. A log over 8 MiB keeps its end (where a failure is).
"""

import os
import re
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from orcus.common.i18n import t
from orcus.common.paths import OrcusPaths

KEEP = 5                   # bundles kept in <root>\diag
ORCUS_LOGS = 30            # the newest Orcus logs
EMULATOR_LOGS = 5
REPORTS = 3                # the newest boot-check reports (logs\*.json)
MAX_FILE_BYTES = 8 << 20
_ACCOUNT = re.compile(r"appinventor\.ai_[A-Za-z0-9_]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def masker(env: dict[str, str] | None = None) -> Callable[[str], str]:
    env = dict(os.environ) if env is None else env
    names = [(env.get(k, ""), f"<{tag}>") for k, tag in (("USERNAME", "user"), ("COMPUTERNAME", "pc"))]
    words = [(re.compile(rf"\b{re.escape(n)}\b", re.IGNORECASE), tag) for n, tag in names if len(n) >= 3]

    def mask(text: str) -> str:
        text = _ACCOUNT.sub("appinventor.ai_<account>", text)
        text = _EMAIL.sub("<email>", text)
        for pattern, tag in words:
            text = pattern.sub(tag, text)
        return text
    return mask


def _newest(files: list[Path], n: int) -> list[Path]:
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)[:n]


def chosen(paths: OrcusPaths) -> list[tuple[str, Path]]:
    """(name in the zip, file) for every file that goes in."""
    logs = [f for f in paths.logs.glob("*.log") if f.is_file()] if paths.logs.is_dir() else []
    emulator = [f for f in logs if f.name.startswith("emulator-")]
    orcus = [f for f in logs if f not in emulator]
    out = [(f"logs/{f.name}", f) for f in _newest(orcus, ORCUS_LOGS) + _newest(emulator, EMULATOR_LOGS)]
    reports = [f for f in paths.logs.glob("*.json") if f.is_file()] if paths.logs.is_dir() else []
    out += [(f"logs/{f.name}", f) for f in _newest(reports, REPORTS)]
    if paths.state.is_dir():
        out += [(f"state/{f.name}", f) for f in sorted(paths.state.glob("*.json")) if f.is_file()]
    config = paths.avd_data / "config.ini"
    if config.is_file():
        out.append(("avd/config.ini", config))
    return out


def make_bundle(paths: OrcusPaths, report: str, doctor: list[str], *,
                mask: Callable[[str], str] | None = None) -> Path:
    mask = mask or masker()
    out_dir = paths.root / "diag"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"orcus-diag-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", mask(report))
        z.writestr("doctor.txt", mask("\n".join(doctor)))
        for name, f in chosen(paths):
            try:
                with open(f, "rb") as fin:
                    size = fin.seek(0, os.SEEK_END)
                    fin.seek(max(0, size - MAX_FILE_BYTES))
                    data = fin.read()
            except OSError as e:   # a log being written or locked: say so in its place
                z.writestr(name + ".unreadable.txt", str(e))
                continue
            head = t("[처음 {n:,} B는 뺐습니다]", n=size - MAX_FILE_BYTES) + "\n" if size > MAX_FILE_BYTES else ""
            z.writestr(name, head + mask(data.decode("utf-8", errors="replace")))
    os.replace(tmp, target)
    for old in _newest([f for f in out_dir.glob("orcus-diag-*.zip")], 1000)[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    return target
