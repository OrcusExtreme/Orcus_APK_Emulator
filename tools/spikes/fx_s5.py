"""Fixture spike S5 (+ A4, S1 and S3 parts), throwaway, on the web-IDE builds of the three fixtures.

A4/S5: install with and without -g, launch times, Screen1 resumed. HelloPurr tap -> sound + vibration record.
S1: TalkToMe and OrcusProbe speaking (device locale ko-KR, OrcusProbe Language "ko"), Korean voice download.
S3 (technical half): does the guest produce audio (gRPC streamAudio peaks) while the meow / TTS plays?
S5: OrcusProbe's error buttons -> exact logcat lines (E/Form numbers, RuntimeErrorAlert, InternalTTS D lines,
SpeechRecognizer paths with and without the providers, ANR), permission dialogs.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s5.py
"""

import json
import sys
import time

from fx_common import (FIXTURES, AudioTap, Live, Logcat, answer_permission, dump, install, key, launch, package_of,
                       paste, permission_dialog, pid_of, resumed, say, screenshot, scroll_to, shake_pulse, tap)
from google.protobuf import empty_pb2

from harness import LaunchMode

APK = {n: FIXTURES / f"{n}.apk" for n in ("HelloPurr", "TalkToMe", "OrcusProbe")}
PKG = {n: package_of(a) for n, a in APK.items()}
PROBE = PKG["OrcusProbe"]
TTS_PKG, ASI_PKG = "com.google.android.tts", "com.google.android.as"
report: dict[str, object] = {}


def section(name: str, fn, *a) -> None:   # type: ignore[no-untyped-def]
    say(name)
    try:
        report[name] = fn(*a)
    except Exception as e:
        report[name] = f"FAILED {type(e).__name__}: {e}"


def short(lines: list[str], n: int = 12) -> list[str]:
    return [line[19:240] for line in lines[:n]]   # drop the date/time prefix


# --- HelloPurr: A4, install/launch, sound + vibration ------------------------------------------------------
def hello(live: Live, log: Logcat, audio: AudioTap) -> dict[str, object]:
    pkg = PKG["HelloPurr"]
    r: dict[str, object] = {"install_no_g": install(live, APK["HelloPurr"], False)}
    live.adb("uninstall", pkg, timeout_s=60)
    r["install_g"] = install(live, APK["HelloPurr"], True)
    r["launch"] = launch(live, pkg)
    time.sleep(1.5)
    r["resumed"] = resumed(live)
    d = dump(live)
    r["texts"] = d.texts()[:6]
    btn = d.find(cls="android.widget.Button")
    assert btn is not None, "no kitty button"
    r["button_bounds"] = btn.bounds
    screenshot(live, "hellopurr")
    t = time.monotonic()
    r["audio_before_tap"] = audio.peak(t - 3, t)
    tap(live, *btn.center)
    time.sleep(2.5)
    r["audio_after_tap"] = audio.peak(t, time.monotonic())
    r["vibrator"] = [line.strip()[:200] for line in live.adb(
        "shell", f"dumpsys vibrator_manager | grep -i -m6 -E '{pkg}|lastVibration|vibration effect'").out.splitlines()]
    r["app_log"] = short(log.since(t, pid=pid_of(live, pkg)))
    live.shell("am", "force-stop", pkg)
    r["relaunch"] = launch(live, pkg)                       # warm-ish start (process was killed)
    live.shell("am", "force-stop", pkg)
    return r


# --- TalkToMe: S1 with the device locale, shake -> speak -------------------------------------------------------
def talk(live: Live, log: Logcat, audio: AudioTap) -> dict[str, object]:
    pkg = PKG["TalkToMe"]
    r: dict[str, object] = {"install": install(live, APK["TalkToMe"], True), "launch": launch(live, pkg)}
    time.sleep(1.5)
    d = dump(live)
    box, btn = d.find(cls="android.widget.EditText"), d.find(text="Talk To Me")
    assert box is not None and btn is not None, d.texts()
    tap(live, *box.center)
    time.sleep(0.8)
    paste(live, "Hello from Orcus")
    time.sleep(0.8)
    r["typed"] = dump(live).find(cls="android.widget.EditText").text   # type: ignore[union-attr]
    t = time.monotonic()
    tap(live, *btn.center)
    time.sleep(8)
    pid = pid_of(live, pkg)
    r["speak_log"] = short(log.since(t, pid=pid, pattern=r"InternalTTS|TextToSpeech|TTS"), 10)
    r["tts_engine_log"] = short(log.since(t, pid=pid_of(live, TTS_PKG)), 12)
    r["speak_audio"] = audio.peak(t, time.monotonic())
    t = time.monotonic()
    r["shake"] = shake_pulse(live, 16.0)
    time.sleep(6)
    r["shake_log"] = short(log.since(t, pid=pid, pattern=r"InternalTTS|Shak"), 8)
    r["shake_audio"] = audio.peak(t, time.monotonic())
    screenshot(live, "talktome")
    live.shell("am", "force-stop", pkg)
    return r


# --- OrcusProbe: permissions and error paths -------------------------------------------------------------------
def probe_start(live: Live, fresh: bool) -> dict[str, object]:
    r: dict[str, object] = {}
    if fresh:
        live.adb("uninstall", PROBE, timeout_s=60)
        r["install_no_g"] = install(live, APK["OrcusProbe"], False)
    else:
        live.shell("am", "force-stop", PROBE)
    r["launch"] = launch(live, PROBE)
    time.sleep(2)
    d = dump(live, PROBE)
    dlg = permission_dialog(d)
    if dlg:
        r["permission_dialog_at_start"] = dlg
        r["answered"] = answer_permission(live, d, allow=True)
        time.sleep(1)
    return r


def press(live: Live, log: Logcat, text: str, pattern: str, wait_s: float, *, after_s: float = 1.0,
          shot: str | None = None) -> dict[str, object]:
    """Scroll to a button, tap it, wait for a logcat pattern, then record lines, screen texts and any dialog."""
    n = scroll_to(live, text, PROBE)
    pid = pid_of(live, PROBE)
    t = time.monotonic()
    tap(live, *n.center)
    hit = log.wait_for(pattern, t, wait_s)
    time.sleep(after_s)
    if shot:
        screenshot(live, shot)
    r: dict[str, object] = {"seconds_to_hit": round(time.monotonic() - t - after_s, 1) if hit else None,
                            "hit": hit[19:260] if hit else None,
                            "app_lines": short(log.since(t, pid=pid, pattern=r" [WEF] |InternalTTS|Speech|TTS"), 10)}
    try:
        d = dump(live, PROBE)
        r["screen"] = [x for x in d.texts() if not x.startswith(("accel ", "orient "))][:14]
        dlg = permission_dialog(d)
        if dlg:
            r["permission_dialog"] = dlg
    except Exception as e:
        r["screen"] = f"dump failed: {e}"
    return r


def dismiss_dialog(live: Live) -> list[str]:
    """Close an app dialog (RuntimeErrorAlert, recognizer UI) with its button or Back; returns what was there."""
    d = dump(live, PROBE)
    buttons = [n for n in d.nodes if n.cls == "android.widget.Button" and n.rid.startswith("android:id/button")]
    if buttons:
        tap(live, *buttons[0].center)
    else:
        key(live, "GoBack")
    time.sleep(1)
    return [b.text for b in buttons]


def probe_errors(live: Live, log: Logcat, audio: AudioTap) -> dict[str, object]:
    r: dict[str, object] = {"start": probe_start(live, fresh=True)}
    r["resumed"] = resumed(live)
    E = r"E Form\s*: Form .* ErrorOccurred"
    r["scan_1501"] = press(live, log, "scan", E, 8, shot="probe_scan_toast")
    r["bad_intent_601"] = press(live, log, "bad intent", E, 8, shot="probe_601_toast")
    r["record_denied_908"] = press(live, log, "record", r"REQUEST_PERMISSIONS|GrantPermissionsActivity", 6)
    d = dump(live, PROBE)
    r["record_denied_908"]["answered"] = answer_permission(live, d, allow=False)   # type: ignore[index]
    t = time.monotonic()
    hit = log.wait_for(E, t, 6)
    r["record_denied_908"]["after_deny"] = hit[19:260] if hit else None             # type: ignore[index]
    r["speech_new_no_permission"] = press(live, log, "listen (new)", E + r"|REQUEST_PERMISSIONS|GrantPermissions", 8)
    d = dump(live, PROBE)
    if permission_dialog(d):
        r["speech_new_no_permission"]["answered"] = answer_permission(live, d, allow=False)  # type: ignore[index]
        hit = log.wait_for(E, time.monotonic(), 6)
        r["speech_new_no_permission"]["after_deny"] = hit[19:260] if hit else None      # type: ignore[index]
    live.shell("pm", "grant", PROBE, "android.permission.RECORD_AUDIO")
    r["speech_new_granted"] = press(live, log, "listen (new)", E + r"|AfterGettingText|speech ", 15, after_s=3)
    r["speech_legacy"] = press(live, log, "listen (legacy)", r"RECOGNIZE_SPEECH|ActivityManager: START|"
                                                               r"Displayed", 10, after_s=4, shot="probe_speech_legacy")
    r["speech_legacy"]["dismissed"] = dismiss_dialog(live)                          # type: ignore[index]
    r["record_granted"] = press(live, log, "record", r"MediaRecorder|SoundRecorder|E Form", 4, after_s=2)
    r["record_stop"] = press(live, log, "stop", r"AfterSoundRecorded|MediaPlayer|rec ok|E Form", 6, after_s=2)
    r["block_error"] = press(live, log, "block error", r"RuntimeErrorAlert", 8, shot="probe_runtime_error")
    r["block_error"]["dismissed"] = dismiss_dialog(live)                            # type: ignore[index]
    r["after_block_error_resumed"] = resumed(live)
    return r


def probe_tts(live: Live, log: Logcat, audio: AudioTap) -> dict[str, object]:
    """S1 + S5: Korean speech with Google TTS present; then TTS disabled -> 2701 and InternalTTS lines."""
    r: dict[str, object] = {"start": probe_start(live, fresh=False)}
    t = time.monotonic()
    r["speak_ko"] = press(live, log, "speak", r"InternalTTS|tts ", 20, after_s=8)
    r["speak_ko"]["audio"] = audio.peak(t, time.monotonic())                        # type: ignore[index]
    r["tts_engine_log"] = short(log.since(t, pid=pid_of(live, TTS_PKG)), 15)
    r["label_tts"] = dump(live, PROBE).label("tts")
    r["voices"] = press(live, log, "voices", r"langs |E Form", 15, after_s=1)
    r["label_langs"] = dump(live, PROBE).label("langs")
    r["superpacks"] = live.shell("su", "0", "ls", "/data/data/com.google.android.tts/files/superpacks").out.split()[:20]
    r["disable_tts"] = live.shell("pm", "disable-user", "--user", "0", TTS_PKG).out.strip()
    r["restart"] = probe_start(live, fresh=False)
    r["voices_no_tts_2701"] = press(live, log, "voices", r"errorNumber = 2701", 25, after_s=1)
    r["speak_no_tts"] = press(live, log, "speak", r"speak will fail|tts.speak result was an error|"
                                                   r"TTS not initialized", 30, after_s=1)
    r["d_level_internaltts_lines"] = len(log.since(t, pattern=r" D InternalTTS"))
    r["enable_tts"] = live.shell("pm", "enable", TTS_PKG).out.strip()
    return r


def probe_no_speech(live: Live, log: Logcat) -> dict[str, object]:
    """Speech providers disabled: legacy (activity) and service paths."""
    r: dict[str, object] = {"disable": [live.shell("pm", "disable-user", "--user", "0", p).out.strip()
                                        for p in (TTS_PKG, ASI_PKG)]}
    r["providers_left"] = live.adb("shell", "cmd package query-activities -a android.speech.action.RECOGNIZE_SPEECH "
                                            "| grep -c packageName; cmd package query-services -a "
                                            "android.speech.RecognitionService | grep -c packageName").out.split()
    r["restart"] = probe_start(live, fresh=False)
    r["legacy_missing"] = press(live, log, "listen (legacy)", r"RuntimeErrorAlert|FATAL|ActivityNotFound", 10,
                                shot="probe_speech_missing")
    r["legacy_missing"]["dismissed"] = dismiss_dialog(live)                         # type: ignore[index]
    r["restart2"] = probe_start(live, fresh=False)
    r["service_missing"] = press(live, log, "listen (new)", r"E Form|recognition service|SpeechRecognizer", 12)
    r["enable"] = [live.shell("pm", "enable", p).out.strip() for p in (TTS_PKG, ASI_PKG)]
    return r


def probe_anr(live: Live, log: Logcat) -> dict[str, object]:
    r: dict[str, object] = {"start": probe_start(live, fresh=False)}
    n = scroll_to(live, "ANR loop", PROBE)
    t = time.monotonic()
    tap(live, *n.center)
    time.sleep(1)
    for _ in range(4):
        tap(live, 360, 300)
        time.sleep(0.7)
    hit = log.wait_for(r"ANR in ", t, 25)
    r["seconds_to_anr"] = round(time.monotonic() - t, 1) if hit else None
    r["anr_line"] = hit[19:240] if hit else None
    r["anr_lines"] = short(log.since(t, pattern=r"ANR|not responding|Input dispatching timed out"), 8)
    time.sleep(2)
    screenshot(live, "probe_anr")
    live.shell("am", "force-stop", PROBE)
    return r


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["packages"] = PKG
    log = Logcat(live)
    audio = AudioTap(live)
    section("hellopurr", hello, live, log, audio)
    section("talktome", talk, live, log, audio)
    section("probe_errors", probe_errors, live, log, audio)
    section("probe_tts", probe_tts, live, log, audio)
    section("probe_no_speech", probe_no_speech, live, log)
    section("probe_anr", probe_anr, live, log)
    report["audio_stream"] = {"packets": len(audio.peaks), "error": audio.error}
    report["mic_state"] = str(live.stub.getMicrophoneState(empty_pb2.Empty(), timeout=5)).strip()
    report["logcat_backlog_dropped"] = log.backlog
    log.close()
    audio.close()

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
