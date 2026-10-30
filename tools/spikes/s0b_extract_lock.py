"""S0b spike (throwaway): who holds freshly extracted SDK files, and for how long can't we rename the folder?

Extracts the .exe/.dll files of the emulator zip into <home>\\tmp\\s0b_probe\\emulator, then every 0.2 s:
asks Restart Manager which processes hold any of those files, and tries renaming the folder.
Also retries the rename of a leftover <home>\\tmp\\extract_emulator\\emulator (setup's failed move), if any.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s0b_extract_lock.py [D:\\OrcusAPK]
"""

import ctypes
import json
import os
import shutil
import sys
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[union-attr]
HOME = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\OrcusAPK")
ZIP = HOME / "downloads" / "emulator-windows_x64-16428233.zip"
PROBE = HOME / "tmp" / "s0b_probe"

rm = ctypes.WinDLL("rstrtmgr")


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", wintypes.WCHAR * 256),
                ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]


def holders(files: list[str]) -> list[tuple[int, str, str]]:
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        arr = (wintypes.LPCWSTR * len(files))(*files)
        if rm.RmRegisterResources(session, len(files), arr, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(), wintypes.UINT(0), wintypes.DWORD()
        rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if needed.value == 0:
            return []
        infos = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reasons)) != 0:
            return []
        return [(i.Process.dwProcessId, i.strAppName, i.strServiceShortName) for i in infos[: count.value]]
    finally:
        rm.RmEndSession(session)


def try_rename(src: Path, dst: Path) -> str | None:
    try:
        os.rename(src, dst)
        return None
    except OSError as e:
        return f"{type(e).__name__}({getattr(e, 'winerror', None)})"


report: dict[str, object] = {"home": str(HOME)}

leftover = HOME / "tmp" / "extract_emulator" / "emulator"
if leftover.exists():
    moved = leftover.with_name("emulator_probe_move")
    err = try_rename(leftover, moved)
    report["leftover_rename_now"] = err or "ok"
    if err is None:
        os.rename(moved, leftover)   # put it back exactly as setup left it

if PROBE.exists():
    shutil.rmtree(PROBE, ignore_errors=True)
target = PROBE / "emulator"
target.mkdir(parents=True)
t0 = time.monotonic()
written: list[str] = []
with zipfile.ZipFile(ZIP) as zf:
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith((".exe", ".dll")):
            continue
        out = target / Path(info.filename).relative_to("emulator")
        out.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        written.append(str(out))
report["extracted_files"] = len(written)
report["extract_s"] = round(time.monotonic() - t0, 2)

import threading  # noqa: E402

timeline: list[dict[str, object]] = []
seen: dict[str, int] = {}
rm_calls: list[float] = []
renamed_after = None
done = threading.Event()


def watch_holders() -> None:   # Restart Manager is slow; keep it off the rename loop
    while not done.is_set():
        t = time.monotonic()
        for pid, app, svc in holders(written):
            name = f"{app} ({svc or '-'}, pid {pid})"
            seen[name] = seen.get(name, 0) + 1
        rm_calls.append(round(time.monotonic() - t, 2))


watcher = threading.Thread(target=watch_holders, daemon=True)
start = time.monotonic()
watcher.start()
failures: dict[str, int] = {}
while time.monotonic() - start < 120:
    err = try_rename(target, PROBE / "moved")
    if err is None:
        renamed_after = round(time.monotonic() - start, 2)
        break
    failures[err] = failures.get(err, 0) + 1
    if len(timeline) < 3:
        timeline.append({"t": round(time.monotonic() - start, 2), "rename": err})
    time.sleep(0.1)
done.set()
watcher.join(30)
report["rename_failures"] = failures
report["rm_call_seconds"] = rm_calls[:5]

final_dir = PROBE / "moved" if renamed_after is not None else target
present = sum(1 for _ in final_dir.rglob("*") if _.is_file())
report["rename_ok_after_s"] = renamed_after
report["holders_seen"] = seen
report["files_still_present"] = present
report["missing_files"] = len(written) - present
report["timeline_head"] = timeline[:5]
report["timeline_tail"] = timeline[-3:]
print(json.dumps(report, ensure_ascii=False, indent=2))
shutil.rmtree(PROBE, ignore_errors=True)
