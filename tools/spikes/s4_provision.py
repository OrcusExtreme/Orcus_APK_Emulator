"""S4 part 2 (throwaway): provision the golden properly, then verify it.

The first S4 run saved the golden while the -change-locale framework restart was still going on
(settings/lock_settings/location services missing). This run:
1. deletes snapshots\\default_boot (a 2.5 GB ram.img nobody uses?) to see whether any run recreates it;
2. PROVISION boot, then watches for the framework restart (boot_completed / start_count / services);
3. waits until settings, lock_settings and location answer for 5 s in a row, applies the one-time
   settings with read-back checks, sets the RAM markers, saves the golden (Delete, Save, List);
4. GOLDEN boot: markers, settings and locale survived; guest clock skew and two ways to fix it.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_provision.py
"""

import json
import shutil
import time
import uuid
from pathlib import Path

from harness import Live, LaunchMode, say

from orcus.emugrpc.generated import snapshot_service_pb2 as ss

GOLDEN = "orcus_golden"
SNAPS = Path(r"D:\OrcusAPK\avd\orcus36.avd\snapshots")
SERVICES = ("settings", "lock_settings", "location", "package", "activity", "input_method")
report: dict[str, object] = {}


def sh(live: Live, cmd: str, timeout_s: float = 30) -> tuple[int, str]:
    r = live.adb("shell", cmd, timeout_s=timeout_s)
    return r.code, (r.out + r.err).strip()


def services_up(live: Live) -> bool:
    code, out = sh(live, "; ".join(f"service check {s}" for s in SERVICES))
    return code == 0 and out.count(": found") == len(SERVICES)


# (command, read-back command, expected substring of the read-back)
PROVISION = [
    ("settings put system accelerometer_rotation 1", "settings get system accelerometer_rotation", "1"),
    ("settings put system screen_off_timeout 2147483647", "settings get system screen_off_timeout", "2147483647"),
    ("svc power stayon true", "dumpsys power | grep -m1 mStayOn=", "mStayOn=true"),
    ("locksettings set-disabled true", "locksettings get-disabled", "true"),
    ("cmd location set-location-enabled true", "cmd location is-location-enabled", "true"),
    ("settings put global verifier_verify_adb_installs 0", "settings get global verifier_verify_adb_installs", "0"),
    ("settings put global package_verifier_enable 0", "settings get global package_verifier_enable", "0"),
    ("setprop debug.wm.disable_deprecated_target_sdk_dialog true",
     "getprop debug.wm.disable_deprecated_target_sdk_dialog", "true"),
]


def apply_provision(live: Live) -> dict[str, object]:
    out: dict[str, object] = {}
    for cmd, check, want in PROVISION:
        deadline = time.monotonic() + 30
        while True:
            code, text = sh(live, cmd)
            _, back = sh(live, check)
            if code == 0 and want in back:
                out[cmd.split()[0] + " " + cmd.split()[2] if len(cmd.split()) > 2 else cmd] = f"ok ({back[:40]})"
                break
            if time.monotonic() > deadline:
                out[cmd] = f"FAILED code={code} {text[:80]} / {back[:60]}"
                break
            time.sleep(1)
    return out


def main() -> None:
    global report
    default_boot = SNAPS / "default_boot"
    report["default_boot_before_mb"] = round(sum(f.stat().st_size for f in default_boot.rglob("*") if f.is_file()) / 1e6) \
        if default_boot.exists() else 0
    shutil.rmtree(default_boot, ignore_errors=True)
    provision_id = uuid.uuid4().hex[:12]

    with Live(mode=LaunchMode.PROVISION) as live:
        t_boot = time.monotonic()
        report["provision_boot"] = dict(live.timings)
        timeline: list[str] = []
        restarted = False
        first_count = sh(live, "getprop sys.system_server.start_count")[1]
        while time.monotonic() - t_boot < 90:
            count = sh(live, "getprop sys.system_server.start_count")[1]
            booted = sh(live, "getprop sys.boot_completed")[1]
            up = services_up(live)
            locale = sh(live, "getprop persist.sys.locale")[1]
            timeline.append(f"+{time.monotonic() - t_boot:4.1f}s count={count} booted={booted} services={up} locale={locale}")
            if count != first_count or not up or booted != "1":
                restarted = True
            if restarted and up and booted == "1" and locale == "ko-KR":
                stable = all(services_up(live) for _ in range(5))   # five checks, about 5 s
                if stable:
                    break
            time.sleep(1)
        report["restart_timeline"] = timeline[:3] + ["..."] + timeline[-4:] if len(timeline) > 8 else timeline
        report["restart_seen"] = restarted
        report["ready_after_boot_s"] = round(time.monotonic() - t_boot, 1)
        report["provision"] = apply_provision(live)
        sh(live, "setprop debug.orcus.session ''")
        sh(live, f"setprop debug.orcus.golden {provision_id}")
        report["markers"] = [sh(live, "getprop debug.orcus.golden")[1], sh(live, "getprop debug.orcus.session")[1]]
        live.snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=60)
        t = time.monotonic()
        saved = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=300)
        report["save"] = {"success": saved.success, "err": saved.err.decode(errors="replace"),
                          "seconds": round(time.monotonic() - t, 1)}
    report["default_boot_after_provision"] = default_boot.exists()

    with Live(mode=LaunchMode.GOLDEN) as live:
        report["golden_boot"] = dict(live.timings)
        report["golden_markers"] = [sh(live, "getprop debug.orcus.golden")[1], sh(live, "getprop debug.orcus.session")[1]]
        report["golden_settings"] = {check: sh(live, check)[1][:40] for _, check, _ in PROVISION}
        report["golden_locale"] = sh(live, "getprop persist.sys.locale")[1]
        skew0 = int(sh(live, "date +%s")[1] or 0) - time.time()
        code, text = sh(live, f"cmd alarm set-time {int(time.time() * 1000)}")
        skew1 = int(sh(live, "date +%s")[1] or 0) - time.time()
        code2, text2 = sh(live, f"su 0 date @{int(time.time())}")
        skew2 = int(sh(live, "date +%s")[1] or 0) - time.time()
        report["clock"] = {"skew_after_load_s": round(skew0, 1),
                           "cmd_alarm_set_time": [code, text[:80], round(skew1, 1)],
                           "su_date": [code2, text2[:80], round(skew2, 1)],
                           "auto_time": sh(live, "settings get global auto_time")[1]}
    report["default_boot_after_golden"] = default_boot.exists()
    report["provision_id"] = provision_id
    report["golden_mb"] = round(sum(f.stat().st_size for f in (SNAPS / GOLDEN).rglob("*") if f.is_file()) / 1e6, 1)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
