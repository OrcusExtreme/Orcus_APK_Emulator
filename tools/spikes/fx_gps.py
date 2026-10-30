"""Fixture spike S7 follow-up 3 (throwaway).

1. Location: neither gRPC setGps nor console `geo fix` reached the guest (gps provider enabled, last location
   null, GOLDEN and COLD). What does the app request, is there a GNSS HAL, what does logcat say, and does the
   framework's test provider (`cmd location providers ...`) work as an injection path instead?
2. Rotation buttons with STEP fire App Inventor Shaking (180 deg = 19.6 m/s2 jump). Does SMOOTH avoid it?

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_gps.py
"""

import json
import re
import sys
import time

from fx_common import (FIXTURES, OUT, Live, Logcat, dump, install, launch, package_of, rotate, say, scroll_to,
                       tap)

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
report: dict[str, object] = {}


def sh(cmd: str, timeout_s: float = 20) -> str:
    r = live.adb("shell", cmd, timeout_s=timeout_s)
    return (r.out + r.err).strip()


def loc_label() -> str | None:
    return dump(live, PROBE).label("loc")


def location() -> dict[str, object]:
    r: dict[str, object] = {}
    log = Logcat(live, settle_s=1.5)
    t = time.monotonic()
    live.stub.setGps(ec.GpsState(latitude=37.5665, longitude=126.9780, altitude=38.0, satellites=8), timeout=5)
    time.sleep(3)
    full = sh("dumpsys location", 30)
    (OUT / "gps_dumpsys_location.txt").write_text(full, encoding="utf-8")
    r["app_requests"] = [line.strip()[:200] for line in full.splitlines() if PROBE.split(".")[-1] in line][:6]
    r["gnss_hal"] = sh("service list | grep -i -E 'gnss|location'")[:400].splitlines()[:6]
    r["gnss_log"] = [line[19:220] for line in log.since(t, pattern=r"(?i)gnss|gps|nmea|qemud")][:12]
    r["label_after_setGps"] = loc_label()
    # framework test provider: the shell needs the mock-location app op
    r["appops"] = sh("appops set com.android.shell android:mock_location allow; appops get com.android.shell "
                     "android:mock_location")
    for provider in ("gps", "fused"):
        r[f"add_{provider}"] = sh(f"cmd location providers add-test-provider {provider}") or "ok"
        r[f"enable_{provider}"] = sh(f"cmd location providers set-test-provider-enabled {provider} true") or "ok"
    t = time.monotonic()
    for i in range(3):
        for provider in ("gps", "fused"):
            sh(f"cmd location providers set-test-provider-location {provider} --location 37.5665,126.9780 "
               f"--accuracy 5")
        time.sleep(1.2)
    r["label_after_test_provider"] = loc_label()
    r["seconds"] = round(time.monotonic() - t, 1)
    r["app_log_after"] = [line[19:200] for line in log.since(t, pattern=r"(?i)location")][:6]
    log.close()
    return r


def smooth_rotation_shakes() -> dict[str, object]:
    out: dict[str, object] = {}
    rotate(live, 0, 0, 0)
    time.sleep(2)
    tap(live, *scroll_to(live, "strong", PROBE, swipes=2).center)    # the most sensitive band, 5-13 m/s2
    time.sleep(0.5)

    def count() -> int:
        m = re.match(r"shake (\d+)", dump(live, PROBE).label("shake") or "")
        return int(m.group(1)) if m else -1

    for interp in ("STEP", "SMOOTH"):
        for z in (90, 180):
            before = count()
            mode = getattr(ec.PhysicalModelValue, interp)
            for target in (z, 0):
                live.stub.setPhysicalModel(ec.PhysicalModelValue(
                    target=ec.PhysicalModelValue.ROTATION, value=ec.ParameterValue(data=[0, 0, target]),
                    interpolation=mode), timeout=5)
                time.sleep(3.0)
            out[f"{interp}_0_{z}_0"] = count() - before
    return out


with Live(mode=LaunchMode.GOLDEN) as live:
    report["install"] = install(live, APK, grant=True)
    launch(live, PROBE)
    time.sleep(2.5)
    for name, fn in (("location", location), ("smooth_rotation_shakes", smooth_rotation_shakes)):
        say(name)
        try:
            report[name] = fn()
        except Exception as e:
            report[name] = f"FAILED {type(e).__name__}: {e}"

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
