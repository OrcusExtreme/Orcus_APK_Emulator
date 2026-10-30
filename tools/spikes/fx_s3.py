"""Fixture spike S3, technical half (throwaway): OrcusProbe recording with the virtual mic off (default) and on
(`setMicrophoneState(realAudioEnabled=true)`), measured by the guest's audio output while the recording plays back.

Whether the PC speakers actually sound is a human check (tools/spikes/fx_s3_listen.py).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s3.py
"""

import json
import sys
import time

from fx_common import (FIXTURES, AudioTap, Live, Logcat, dump, install, launch, package_of, say, scroll_to, tap)
from google.protobuf import empty_pb2

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
report: dict[str, object] = {}


def record_and_play(live: Live, log: Logcat, audio: AudioTap, seconds: float) -> dict[str, object]:
    tap(live, *scroll_to(live, "record", PROBE).center)
    t_rec = time.monotonic()
    time.sleep(seconds)
    rec_window = audio.peak(t_rec, time.monotonic())
    stop = dump(live, PROBE).find(text="stop")
    assert stop is not None
    t = time.monotonic()
    tap(live, *stop.center)
    time.sleep(seconds + 1.5)                 # AfterSoundRecorded -> Player1.Start plays the recording back
    files = live.adb("shell", f"su 0 find /storage/emulated/0 /data/data/{PROBE} -type f -mmin -1 "
                              f"\\( -name '*.3gp' -o -name '*.m4a' -o -name '*.mp4' -o -name '*.aac' \\) "
                              f"-exec ls -l {{}} \\;", timeout_s=20).out.strip().splitlines()
    return {"output_while_recording": rec_window, "playback_output": audio.peak(t, time.monotonic()),
            "label": dump(live, PROBE).label("rec"),
            "app_lines": [line[19:200] for line in log.since(t_rec, pattern=r"(?i)SoundRecorder|MediaRecorder|"
                                                                           r"Player|E Form")][:8],
            "new_files": files[:6]}


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["install"] = install(live, APK, grant=True)
    report["launch"] = launch(live, PROBE)
    time.sleep(2)
    log = Logcat(live)
    audio = AudioTap(live)
    report["mic_default"] = str(live.stub.getMicrophoneState(empty_pb2.Empty(), timeout=5)).strip() or "realAudioEnabled: false"
    say("record with the mic off")
    report["mic_off"] = record_and_play(live, log, audio, 3.0)
    live.stub.setMicrophoneState(ec.MicrophoneState(realAudioEnabled=True), timeout=5)
    report["mic_set"] = str(live.stub.getMicrophoneState(empty_pb2.Empty(), timeout=5)).strip()
    say("record with the mic on")
    report["mic_on"] = record_and_play(live, log, audio, 3.0)
    live.stub.setMicrophoneState(ec.MicrophoneState(realAudioEnabled=False), timeout=5)
    report["audio_stream"] = {"packets": len(audio.peaks), "error": audio.error}
    report["audio_input_devices"] = [line.strip()[:160] for line in live.adb(
        "shell", "dumpsys media.audio_policy | grep -i -m8 -E 'input|mic'", timeout_s=15).out.splitlines()]
    log.close()
    audio.close()

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
