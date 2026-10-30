"""S9 spike (throwaway): the private adb server.

Questions (plan.md S9): does `adb nodaemon server` run inside our job? Does the emulator register itself
with 15037? How fast does `host:emulator:5641` re-register after a server restart, with the startup scan
off (MAX_PORT=5553)? Does a `-H 127.0.0.1` client refuse to fork a server? Is the shared key
(ADB_VENDOR_KEYS) accepted? Is the default 5037 server isolated from emulator-5640 and vice versa?
Does the emulator start an adb of its own when ours is running?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s9_adb.py
"""

import json
import os
import socket
import subprocess
import time

from harness import Live, LaunchMode, register_emulator, say

from orcus.app import compose
from orcus.common import childenv, proc, winjob

report: dict[str, object] = {}


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def adb_pids(live: Live) -> list[str]:
    return [f"{pid} {img}" for pid, img in winjob.processes_under(live.paths.platform_tools)]


with Live(mode=LaunchMode.COLD) as live:
    report["timings"] = live.timings
    report["serial"] = live.serial
    assert live.adb_server is not None
    handle = winjob.kernel32.OpenProcess(winjob.PROCESS_QUERY_LIMITED_INFORMATION, False, live.adb_server.pid)
    report["adb_server_in_our_job"] = compose._job.contains(handle) if compose._job else None   # noqa: SLF001
    winjob.close_handle(handle)
    report["devices_after_boot"] = live.adb("devices", "-l").out.strip().splitlines()
    report["get_state"] = live.adb("get-state").out.strip()   # 'device' = shared key accepted
    report["adb_processes_while_running"] = adb_pids(live)
    pub = (live.paths.adbkey.with_suffix(".pub")).read_text(encoding="utf-8").split()[0]
    log_text = live.emulator_log.read_text(encoding="utf-8", errors="replace")
    report["emulator_sent_shared_key"] = pub[:40] in log_text.replace("\n", "")

    # Restart the server: the scan is off, so only the re-registration message brings the device back.
    live.adb_server.kill()
    live.adb_server.wait(10)
    t_down = time.monotonic()
    client = live.adb("devices", timeout_s=10)
    report["client_with_server_down"] = (client.code, (client.out + client.err).strip()[-200:])
    report["server_forked_by_client"] = listening(live.ports.adb_server)
    env = childenv.adb_server_env(live.paths, os.environ, adb_server_port=live.ports.adb_server,
                                  strip_dirs=childenv.app_dirs_to_strip())
    live.adb_server = proc.spawn([str(live.paths.adb_exe), "-P", str(live.ports.adb_server), "nodaemon", "server"],
                                 env=env, log_path=live.paths.logs / "adb-server.log")
    while not listening(live.ports.adb_server):
        time.sleep(0.05)
    t_up = time.monotonic()
    report["server_restart_s"] = round(t_up - t_down, 2)
    time.sleep(1.0)
    report["devices_before_reregister"] = live.adb("devices").out.strip().splitlines()
    t0 = time.monotonic()
    report["register_reply"] = register_emulator(live.ports.adb_server, live.ports.adb)
    state = ""
    while time.monotonic() - t0 < 20:
        state = live.adb("get-state", timeout_s=5).out.strip()
        if state == "device":
            break
        time.sleep(0.1)
    report["reregister_to_device_s"] = round(time.monotonic() - t0, 2)
    report["state_after_reregister"] = state
    report["shell_after_reregister"] = live.shell("getprop", "ro.build.version.release").out.strip()

    # Other servers: our list holds only our emulator; the default 5037 server must not see it.
    report["our_devices"] = live.adb("devices").out.strip().splitlines()
    report["mdns_check"] = live.adb("mdns", "check").out.strip()
    started_5037 = False
    if not listening(5037):
        clean = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ANDROID_", "ADB_"))}
        subprocess.run([str(live.paths.adb_exe), "-P", "5037", "start-server"], env=clean, capture_output=True,
                       timeout=30, creationflags=proc.CREATE_NO_WINDOW)
        started_5037 = True
        time.sleep(3)   # let its startup scan of 5555-5585 run a little
    ext = proc.run([str(live.paths.adb_exe), "-P", "5037", "devices"], timeout_s=30)
    report["external_5037_started_by_us"] = started_5037
    report["external_5037_devices"] = ext.out.strip().splitlines()
    if started_5037:
        proc.run([str(live.paths.adb_exe), "-P", "5037", "kill-server"], timeout_s=30)
    report["adb_processes_end"] = adb_pids(live)

report["sdk_processes_after_stop"] = [f"{p} {i}" for p, i in winjob.processes_under(live.paths.sdk)]
print(json.dumps(report, ensure_ascii=False, indent=2))
