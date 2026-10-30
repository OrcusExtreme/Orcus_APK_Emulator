"""S5 + S8 partial spike (throwaway), without the user's App Inventor fixtures.

S5 with MITAI2Companion.apk (a real App Inventor build): install time with and without -g, launcher
resolution, `am start -W`, Play Protect / verifier prompts, logcat over gRPC, the exact `am crash` lines.
S8 on a stock EditText (Settings search): Korean via setClipboard + Paste key vs Ctrl+V, read back with
uiautomator.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s5_s8_partial.py
"""

import json
import re
import threading
import time
from pathlib import Path

from harness import Live, LaunchMode, say

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = Path(r"D:\OrcusDev\fixtures\MITAI2Companion.apk")
PKG = "edu.mit.appinventor.aicompanion3"
KOREAN = "안녕하세요 Orcus"
report: dict[str, object] = {}
golden = Path(r"D:\OrcusAPK\avd\orcus36.avd\snapshots\orcus_golden").exists()


def section(name: str, fn, *a):   # type: ignore[no-untyped-def]
    try:
        report[name] = fn(*a)
    except Exception as e:
        report[name] = f"FAILED {type(e).__name__}: {e}"


def ui_texts(live: Live) -> list[str]:
    live.adb("shell", "uiautomator dump /sdcard/ui.xml", timeout_s=30)
    xml = live.adb("exec-out", "cat /sdcard/ui.xml", timeout_s=30).out
    return [t for t in re.findall(r'text="([^"]*)"', xml) if t]


def install(live: Live, grant: bool) -> dict[str, object]:
    live.adb("uninstall", PKG, timeout_s=60)
    t = time.monotonic()
    r = live.adb("install", "-r", *(["-g"] if grant else []), str(APK), timeout_s=180)
    return {"grant": grant, "seconds": round(time.monotonic() - t, 2), "code": r.code,
            "out": (r.out + r.err).strip()[-200:]}


def launch(live: Live) -> dict[str, object]:
    comp = live.shell("cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER",
                      PKG).out.strip().splitlines()[-1]
    t = time.monotonic()
    r = live.shell("am", "start", "-W", "-n", comp, timeout_s=60)
    total = re.search(r"TotalTime: (\d+)", r.out)
    time.sleep(3)
    return {"component": comp, "am_start_s": round(time.monotonic() - t - 3, 2),
            "TotalTime_ms": int(total.group(1)) if total else None, "status": re.findall(r"Status: \w+", r.out),
            "screen_texts": ui_texts(live)[:12],
            "verifier": [live.shell("settings", "get", "global", k).out.strip()
                         for k in ("verifier_verify_adb_installs", "package_verifier_enable")]}


def logcat_and_crash(live: Live) -> dict[str, object]:
    lines: list[str] = []
    stream = live.stub.streamLogcat(ec.LogMessage(sort=ec.LogMessage.Parsed), timeout=40)

    def pump() -> None:
        try:
            for msg in stream:
                for e in msg.entries:
                    lines.append(f"{ec.LogcatEntry.LogLevel.Name(e.level)} {e.tag} pid={e.pid}: {e.msg[:160]}")
                if msg.contents:
                    lines.extend(msg.contents.splitlines()[:50])
        except Exception:
            pass
    th = threading.Thread(target=pump, daemon=True)
    th.start()
    time.sleep(2)
    pid = live.shell("pidof", PKG).out.strip()
    live.shell("am", "crash", PKG)
    time.sleep(5)
    stream.cancel()
    th.join(5)
    crash = [line for line in lines if "FATAL" in line or "AndroidRuntime" in line or "crash" in line.lower()]
    return {"pid_before": pid, "lines_total": len(lines), "crash_lines": crash[:12],
            "pid_after": live.shell("pidof", PKG).out.strip(), "sample": lines[:8]}


def paste(live: Live, use_ctrl_v: bool) -> dict[str, object]:
    live.shell("am", "start", "-W", "-a", "android.settings.APP_SEARCH_SETTINGS")
    time.sleep(2)
    live.stub.setClipboard(ec.ClipData(text=KOREAN), timeout=5)
    time.sleep(0.1)

    def key(name: str, kind: int = ec.KeyboardEvent.keypress) -> ec.InputEvent:
        return ec.InputEvent(key_event=ec.KeyboardEvent(eventType=kind, key=name))
    events = ([key("Control", ec.KeyboardEvent.keydown), key("v"), key("Control", ec.KeyboardEvent.keyup)]
              if use_ctrl_v else [key("Paste")])
    live.stub.streamInputEvent(iter(events), timeout=10)
    time.sleep(1.5)
    texts = ui_texts(live)
    live.shell("input", "keyevent", "KEYCODE_BACK")
    live.shell("input", "keyevent", "KEYCODE_HOME")
    return {"method": "Ctrl+V" if use_ctrl_v else "Paste key", "korean_found": KOREAN in texts,
            "texts": [t for t in texts if "Orcus" in t or "안녕" in t or "클립" in t or "clipboard" in t.lower()][:6]}


with Live(mode=LaunchMode.GOLDEN if golden else LaunchMode.COLD) as live:
    report["boot"] = {"mode": "golden" if golden else "cold", **live.timings}
    live.shell("settings", "put", "system", "screen_off_timeout", "2147483647")
    live.shell("svc", "power", "stayon", "true")
    say("install x2")
    section("install_no_g", install, live, False)
    section("install_with_g", install, live, True)
    say("launch")
    section("launch", launch, live)
    say("logcat + crash")
    section("logcat_crash", logcat_and_crash, live)
    live.adb("uninstall", PKG, timeout_s=60)
    say("paste")
    section("paste_key", paste, live, False)
    section("paste_ctrl_v", paste, live, True)

print(json.dumps(report, ensure_ascii=False, indent=2))
