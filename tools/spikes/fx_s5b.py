"""Fixture spike S5 follow-up (throwaway): the two items fx_s5 missed.

- 908: deny RECORD_AUDIO in the dialog for SoundRecorder.Start and SpeechRecognizer.GetText; the error line comes
  right after the tap on [Deny], so the log window starts before that tap this time.
- ANR: OrcusProbe's ANR loop button plus a few taps; the "ANR in" line, the time to it, the dialog.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s5b.py
"""

import json
import sys
import time

from fx_common import (FIXTURES, Live, Logcat, answer_permission, dump, install, launch, package_of,
                       permission_dialog, pid_of, say, screenshot, scroll_to, tap)

from harness import LaunchMode

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
E = r"E Form\s*: Form .* ErrorOccurred"
report: dict[str, object] = {}


def deny_for(live: Live, log: Logcat, button: str) -> dict[str, object]:
    tap(live, *scroll_to(live, button, PROBE).center)
    time.sleep(1.5)
    d = dump(live, PROBE)
    r: dict[str, object] = {"dialog": permission_dialog(d)}
    t = time.monotonic()
    r["answered"] = answer_permission(live, d, allow=False)
    hit = log.wait_for(E, t, 8)
    r["error_line"] = hit[19:260] if hit else None
    r["seconds"] = round(time.monotonic() - t, 1) if hit else None
    r["other_app_lines"] = [line[19:220] for line in log.since(t, pid=pid_of(live, PROBE),
                                                                pattern=r" [WE] (?!Toast)")][:6]
    screenshot(live, f"s5b_908_{button.replace(' ', '_').replace('(', '').replace(')', '')}")
    return r


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    log = Logcat(live)
    report["install_no_g"] = install(live, APK, grant=False)
    report["launch"] = launch(live, PROBE)
    time.sleep(2)
    d = dump(live, PROBE)
    if permission_dialog(d):
        report["location_dialog"] = answer_permission(live, d, allow=True)
    say("908 record")
    try:
        report["record_denied"] = deny_for(live, log, "record")
    except Exception as e:
        report["record_denied"] = f"FAILED {type(e).__name__}: {e}"
    say("908 listen (new)")
    try:
        report["speech_denied"] = deny_for(live, log, "listen (new)")
    except Exception as e:
        report["speech_denied"] = f"FAILED {type(e).__name__}: {e}"
    say("ANR")
    try:
        live.shell("am", "force-stop", PROBE)
        launch(live, PROBE)
        time.sleep(2)
        n = scroll_to(live, "ANR loop", PROBE)
        t = time.monotonic()
        tap(live, *n.center)
        time.sleep(1)
        for _ in range(4):
            tap(live, 360, 300)
            time.sleep(0.7)
        hit = log.wait_for(r"ANR in ", t, 30)
        report["anr"] = {"seconds_to_line": round(time.monotonic() - t, 1) if hit else None,
                         "line": hit[19:240] if hit else None,
                         "lines": [line[19:240] for line in log.since(t, pattern=r"ANR|not responding|"
                                                                              r"Input dispatching timed out")][:8]}
        time.sleep(3)
        screenshot(live, "s5b_anr")
        live.shell("am", "force-stop", PROBE)
    except Exception as e:
        report["anr"] = f"FAILED {type(e).__name__}: {e}"
    log.close()

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
