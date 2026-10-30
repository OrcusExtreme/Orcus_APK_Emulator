"""S4 spike (throwaway): golden snapshot lifecycle, measured.

1. PROVISION boot (-change-locale ko-KR): boot time, locale switch, framework restart signal.
2. One-time device settings (draft of engine/provision.py), RAM markers, then gRPC SaveSnapshot
   (after Delete + List), its reply, a second save of the same name (expect refusal), sizes.
3. GOLDEN boots (-snapshot orcus_golden -force-snapshot-load -no-snapshot-save) x3: time to adb device,
   markers present, guest clock vs host.
4. Reset while running: LoadSnapshot(golden) + nonce check + adb state.
5. Experiments on throwaway names only: -snapshot without -no-snapshot-save (does exit save?), and
   -force-snapshot-load of a damaged snapshot (is it deleted?).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/s4_golden.py
"""

import json
import shutil
import time
import uuid
from pathlib import Path

from google.protobuf import empty_pb2
from harness import Live, LaunchMode, say

from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

GOLDEN = "orcus_golden"
report: dict[str, object] = {}


def dir_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6, 1) if path.exists() else 0.0


def prop(live: Live, name: str) -> str:
    return live.shell("getprop", name).out.strip()


def snapshots(live: Live) -> list[str]:
    reply = live.snap.ListSnapshots(ss.SnapshotFilter(statusFilter=ss.SnapshotFilter.All), timeout=30)
    return [f"{d.snapshot_id}:{ss.SnapshotDetails.LoadStatus.Name(d.status)}" for d in reply.snapshots]


def provision_settings(live: Live) -> dict[str, str]:
    cmds = {
        "rotation": "settings put system accelerometer_rotation 1",
        "screen_off": "settings put system screen_off_timeout 2147483647",
        "stayon": "svc power stayon true",
        "lockscreen": "locksettings set-disabled true",
        "location": "cmd location set-location-enabled true",
        "verifier1": "settings put global verifier_verify_adb_installs 0",
        "verifier2": "settings put global package_verifier_enable 0",
        "sdk_dialog": "setprop debug.wm.disable_deprecated_target_sdk_dialog true",
    }
    out = {}
    for key, cmd in cmds.items():
        r = live.adb("shell", cmd)
        out[key] = f"code={r.code} {(r.out + r.err).strip()[:120]}"
    return out


avd = Path(r"D:\OrcusAPK\avd\orcus36.avd")
snapdir = avd / "snapshots"
report["avd_mb_before"] = dir_mb(avd)
provision_id = uuid.uuid4().hex[:12]

# 1-2. provisioning boot and golden save ------------------------------------------------------------
with Live(mode=LaunchMode.PROVISION) as live:
    report["provision_timings"] = dict(live.timings)
    t0 = time.monotonic()
    locale = prop(live, "persist.sys.locale")
    while locale != "ko-KR" and time.monotonic() - t0 < 120:
        time.sleep(1)
        locale = prop(live, "persist.sys.locale")
    report["locale_after_s"] = round(time.monotonic() - t0, 1)
    report["locale"] = locale
    report["system_server_start_count"] = prop(live, "sys.system_server.start_count")
    report["timezone"] = prop(live, "persist.sys.timezone")
    report["settings"] = provision_settings(live)
    r1 = live.shell("setprop", "debug.orcus.session", "")
    r2 = live.shell("setprop", "debug.orcus.golden", provision_id)
    report["markers_set"] = [r1.code, r2.code, prop(live, "debug.orcus.golden"), prop(live, "debug.orcus.session")]
    report["snapshots_before_save"] = snapshots(live)
    live.snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=60)
    t = time.monotonic()
    saved = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=300)
    report["save"] = {"success": saved.success, "err": saved.err.decode(errors="replace"),
                      "seconds": round(time.monotonic() - t, 1)}
    again = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=300)
    report["save_same_name_again"] = {"success": again.success, "err": again.err.decode(errors="replace")}
    report["snapshots_after_save"] = snapshots(live)
report["golden_mb"] = dir_mb(snapdir / GOLDEN)
report["avd_mb_after_provision"] = dir_mb(avd)
report["golden_files"] = sorted(f"{p.name}:{round(p.stat().st_size / 1e6, 1)}MB" for p in (snapdir / GOLDEN).glob("*"))

# 3. golden boots -------------------------------------------------------------------------------------
boots = []
for i in range(3):
    with Live(mode=LaunchMode.GOLDEN) as live:
        host_now = time.time()
        guest_now = int(live.shell("date", "+%s").out.strip() or 0)
        boots.append({"timings": dict(live.timings), "golden_marker": prop(live, "debug.orcus.golden"),
                      "session_marker": prop(live, "debug.orcus.session"), "locale": prop(live, "persist.sys.locale"),
                      "clock_skew_s": round(guest_now - host_now, 1),
                      "snapshots": snapshots(live) if i == 0 else None})
        if i == 2:
            # 4. reset while running
            nonce = uuid.uuid4().hex[:8]
            live.shell("setprop", "debug.orcus.session", nonce)
            t = time.monotonic()
            loaded = live.snap.LoadSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=120)
            load_s = round(time.monotonic() - t, 2)
            state, t = "", time.monotonic()
            while time.monotonic() - t < 15:
                state = live.adb("get-state", timeout_s=5).out.strip()
                if state == "device":
                    break
                time.sleep(0.1)
            report["reset"] = {"success": loaded.success, "err": loaded.err.decode(errors="replace"), "load_s": load_s,
                               "adb_back_s": round(time.monotonic() - t, 2), "adb_state": state,
                               "session_after": prop(live, "debug.orcus.session"), "nonce_was": nonce,
                               "golden_after": prop(live, "debug.orcus.golden"),
                               "booted_after": live.status().booted}
report["golden_boots"] = boots
report["golden_marker_expected"] = provision_id

# 5a. -snapshot without -no-snapshot-save on a throwaway name: does exit save over it? -------------------
tmp_name = "s4_tmp_exit_save"
with Live(mode=LaunchMode.COLD) as live:
    live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=tmp_name), timeout=300)
pb = snapdir / tmp_name / "snapshot.pb"
before = pb.stat().st_mtime if pb.exists() else None


def snapshot_without_no_save(argv: list[str]) -> list[str]:
    return [a for a in argv if a not in ("-no-snapshot-load", "-no-snapshot-save")] + ["-snapshot", tmp_name]


try:
    with Live(mode=LaunchMode.COLD, argv_transform=snapshot_without_no_save) as live:
        report["exit_save_boot_timings"] = dict(live.timings)
        live.shell("setprop", "debug.orcus.exit_save_probe", "1")
except Exception as e:
    report["exit_save_boot_error"] = f"{type(e).__name__}: {e}"
after = pb.stat().st_mtime if pb.exists() else None
report["snapshot_flag_without_no_save"] = {"mtime_before": before, "mtime_after": after,
                                           "saved_on_exit": before is not None and after is not None and after > before}

# 5b. forced load of a damaged throwaway snapshot: does the emulator delete it? -------------------------
damaged = "s4_tmp_damaged"
with Live(mode=LaunchMode.COLD) as live:
    live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=damaged), timeout=300)
report["damaged_files_before"] = sorted(f"{p.name}:{p.stat().st_size}" for p in (snapdir / damaged).glob("*"))
ram = snapdir / damaged / "ram.bin"
if ram.exists():
    with open(ram, "r+b") as f:
        f.truncate(1024)
t = time.monotonic()
try:
    with Live(mode=LaunchMode.GOLDEN, boot_timeout_s=90,
              argv_transform=lambda argv: [damaged if a == GOLDEN else a for a in argv]) as live:
        report["damaged_force_load"] = {"booted": True, "marker": prop(live, "debug.orcus.golden")}
except Exception as e:
    report["damaged_force_load"] = {"booted": False, "error": f"{type(e).__name__}: {e}",
                                    "after_s": round(time.monotonic() - t, 1)}
report["damaged_snapshot_still_there"] = (snapdir / damaged).exists()
latest = max(Path(r"D:\OrcusAPK\logs").glob("emulator-spike-*.log"), key=lambda p: p.stat().st_mtime)
report["damaged_log_tail"] = [line for line in latest.read_text(encoding="utf-8", errors="replace").splitlines()
                              if "napshot" in line or "ERROR" in line or "FATAL" in line][-12:]
for name in (tmp_name, damaged):
    shutil.rmtree(snapdir / name, ignore_errors=True)
report["avd_mb_end"] = dir_mb(avd)
print(json.dumps(report, ensure_ascii=False, indent=2))
