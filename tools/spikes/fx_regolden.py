"""Rebuild the golden with the current provisioning list — throwaway, Phase 3a's provisioning in miniature.

Same two-session method as s4_provision_v2.py (PROVISION boot applies the settings, a clean COLD boot saves),
plus guards learned the hard way (16:01 run): never save when a provisioning step failed or the settings did not
persist, and prove the new golden boots (adb, settings) before calling it done.

    --wipe    start from an empty userdata (-wipe-data), like a first install

Also answers: do apps installed in GOLDEN sessions survive into a later COLD boot?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_regolden.py [--wipe]
"""

import json
import sys
import time
import uuid

from fx_common import Logcat, dump, screenshot
from harness import Live, LaunchMode, say
from provision_draft import PROVISION, RAM_PROPS, apply_checked, flush, sh, wait_framework_restart

from orcus.emugrpc.generated import snapshot_service_pb2 as ss

GOLDEN = "orcus_golden"
WIPE = "--wipe" in sys.argv
report: dict[str, object] = {"wipe": WIPE}
provision_id = uuid.uuid4().hex[:12]


def third_party(live: Live) -> list[str]:
    return sorted(line.removeprefix("package:") for line in sh(live, "pm list packages -3")[1].splitlines() if line)


def finish(code: int) -> None:
    report["provision_id"] = provision_id
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(code)


try:
    with Live(mode=LaunchMode.PROVISION, extra_args=("-wipe-data",) if WIPE else (), boot_timeout_s=600) as live:
        report["provision_boot"] = dict(live.timings)
        report["provision_ready_s"] = wait_framework_restart(live)
        leftovers = third_party(live)
        report["third_party_seen_by_provision_boot"] = leftovers
        report["uninstall"] = {p: sh(live, f"pm uninstall {p}")[1] for p in leftovers}
        report["provision"] = apply_checked(live, PROVISION)
        report["flush"] = flush(live)
    if any(v != "ok" for v in report["provision"].values()):   # type: ignore[union-attr]
        say("provisioning failed; the golden was NOT touched")
        finish(1)

    with Live(mode=LaunchMode.COLD) as live:
        report["clean_boot"] = dict(live.timings)
        time.sleep(10)   # settle
        persisted = {check: sh(live, check)[1][:40] for _, check, _ in PROVISION}
        report["persisted"] = persisted
        missing = [check for _, check, want in PROVISION if want not in persisted[check]]
        report["third_party_in_clean_boot"] = third_party(live)
        report["locale"] = sh(live, "getprop persist.sys.locale")[1]
        if missing or report["locale"] != "ko-KR":
            report["not_persisted"] = missing
            say("settings did not persist; the golden was NOT touched")
            finish(1)
        report["ram_props"] = apply_checked(live, RAM_PROPS)
        sh(live, "setprop debug.orcus.session ''")
        sh(live, f"setprop debug.orcus.golden {provision_id}")
        live.snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=60)
        saved = live.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN), timeout=300)
        report["saved"] = {"success": saved.success, "err": saved.err.decode(errors="replace")}
        if not saved.success:
            finish(1)

    say("golden saved; loading it after 20 s")
    time.sleep(20)

    with Live(mode=LaunchMode.GOLDEN, boot_timeout_s=90) as live:
        t0 = time.monotonic()
        report["golden_boot"] = dict(live.timings)
        log = Logcat(live)
        report["markers"] = [sh(live, "getprop debug.orcus.golden")[1], sh(live, "getprop debug.orcus.session")[1]]
        report["settings_in_golden"] = {check: sh(live, check)[1][:40] for _, check, _ in PROVISION}
        time.sleep(25)   # the S4 freeze came 20-25 s after a load; adb must still answer after that
        report["adb_ok_after_30s"] = sh(live, "echo ok")[1]
        report["bluetooth_crash_lines"] = len(log.since(t0, pattern=r"on_hardware_error|Bluetooth stack has died"))
        report["screen_texts"] = dump(live).texts()[:8]
        screenshot(live, "golden_home")
        log.close()
    finish(0 if report["adb_ok_after_30s"] == "ok" else 1)
except Exception as e:
    report["error"] = f"{type(e).__name__}: {e}"
    finish(2)
