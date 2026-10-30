"""Fixture spike S7 follow-up 2 (throwaway).

1. Location: gRPC setGps never produced a location in the guest (all providers "last location=null").
   GOLDEN vs COLD session x gRPC setGps vs console `geo fix`, read back with `dumpsys location` and OrcusProbe.
2. Shake upside down and in landscape with Moderate actually selected (fx_s7b left Weak on).

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s7c.py
"""

import json
import re
import sys
import time

from fx_common import FIXTURES, Live, dump, install, launch, package_of, rotate, say, scroll_to, shake_pulse, tap

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
report: dict[str, object] = {}


def gps_state(live: Live) -> dict[str, object]:
    out = live.adb("shell", "dumpsys location", timeout_s=20).out
    gps = out.split("gps provider:")[1][:1500] if "gps provider:" in out else ""
    return {"gps_enabled": re.findall(r"enabled=\w+", gps)[:2],
            "gps_last": [line.strip()[:140] for line in gps.splitlines() if "last location" in line.lower()][:2],
            "any_last": [line.strip()[:140] for line in out.splitlines()
                         if "last location=Location" in line or "Location[gps" in line][:3]}


def location(live: Live) -> dict[str, object]:
    r: dict[str, object] = {"install": install(live, APK, grant=True)["seconds"]}
    launch(live, PROBE)
    time.sleep(2)
    live.stub.setGps(ec.GpsState(latitude=37.5665, longitude=126.9780, altitude=38.0, satellites=8), timeout=5)
    time.sleep(4)
    r["grpc_setGps"] = {"label": dump(live, PROBE).label("loc"), **gps_state(live),
                        "getGps": str(live.stub.getGps(__import__("google.protobuf.empty_pb2", fromlist=["Empty"])
                                                       .Empty(), timeout=5)).replace("\n", " ")[:160]}
    con = live.adb("emu", "geo", "fix", "126.9800", "37.5700", "38", timeout_s=15)
    time.sleep(4)
    r["console_geo_fix"] = {"reply": (con.out + con.err).strip()[:80], "label": dump(live, PROBE).label("loc"),
                            **gps_state(live)}
    return r


def shakes(live: Live) -> dict[str, object]:
    out: dict[str, object] = {}

    def count() -> int:
        m = re.match(r"shake (\d+)", dump(live, PROBE).label("shake") or "")
        return int(m.group(1)) if m else -1

    rotate(live, 0, 0, 0)
    time.sleep(2)
    tap(live, *scroll_to(live, "moderate", PROBE, swipes=2).center)
    time.sleep(0.5)
    out["sens"] = dump(live, PROBE).label("sens")
    for name, z in (("portrait", 0), ("upside_down", 180), ("landscape", 90), ("reverse_landscape", 270)):
        before = count() if z == 0 else None
        if z == 0:
            out["count_start"] = before
        rotate(live, 0, 0, z)
        time.sleep(2.5)
        pulse = shake_pulse(live, 16)
        time.sleep(1.0)
        rotate(live, 0, 0, 0)
        time.sleep(2.5)
        out[name] = {"base": pulse["base"], "pulse": pulse["pulse"], "count_after": count()}
    return out


for mode in (LaunchMode.GOLDEN, LaunchMode.COLD):
    say(f"{mode.name} session")
    with Live(mode=mode) as live:
        key = mode.name.lower()
        report[f"{key}_boot"] = dict(live.timings)
        try:
            report[f"{key}_location"] = location(live)
        except Exception as e:
            report[f"{key}_location"] = f"FAILED {type(e).__name__}: {e}"
        if mode is LaunchMode.GOLDEN:
            try:
                report["shakes"] = shakes(live)
            except Exception as e:
                report["shakes"] = f"FAILED {type(e).__name__}: {e}"

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
