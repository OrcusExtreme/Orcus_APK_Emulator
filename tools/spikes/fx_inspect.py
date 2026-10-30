"""Fixture check (throwaway): what the web-IDE builds of HelloPurr / TalkToMe / OrcusProbe contain.

Manifest via apkInspector (the parser Phase 3a uses): package, versions, SDK levels, activities with their
screenOrientation, permissions; plus the assets that should have come from the .aia.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_inspect.py
"""

import json
import re
import sys
import zipfile
from pathlib import Path

from apkInspector.axml import parse_apk_for_manifest

FIXTURES = Path(r"D:\OrcusDev\fixtures")
ANDROID = "android:"


def attrs(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([\w:]+)="([^"]*)"', tag))


def inspect(apk: Path) -> dict[str, object]:
    xml = parse_apk_for_manifest(str(apk))
    manifest = attrs(re.search(r"<manifest[^>]*>", xml).group(0))
    sdk = attrs((re.search(r"<uses-sdk[^>]*>", xml) or re.search(r"$^", "")).group(0)) if "<uses-sdk" in xml else {}
    activities = []
    for m in re.finditer(r"<activity[^>]*>", xml):
        a = attrs(m.group(0))
        activities.append({"name": a.get(ANDROID + "name"), "orientation": a.get(ANDROID + "screenOrientation"),
                           "exported": a.get(ANDROID + "exported")})
    launcher = re.search(r'<activity[^>]*android:name="([^"]+)"[^>]*>(?:(?!</activity>).)*'
                         r'android.intent.category.LAUNCHER', xml, re.S)
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
    return {
        "size_kib": apk.stat().st_size // 1024,
        "package": manifest.get("package"),
        "versionCode": manifest.get(ANDROID + "versionCode"),
        "versionName": manifest.get(ANDROID + "versionName"),
        "minSdk": sdk.get(ANDROID + "minSdkVersion"), "targetSdk": sdk.get(ANDROID + "targetSdkVersion"),
        "launcher": launcher.group(1) if launcher else None,
        "activities": activities,
        "permissions": sorted(set(re.findall(r'<uses-permission[^>]*android:name="([^"]+)"', xml))),
        "assets": sorted(n for n in names if n.startswith("assets/") and not n.startswith("assets/external_comps")),
        "dex": sorted(n for n in names if n.endswith(".dex")),
        "native_abis": sorted({n.split("/")[1] for n in names if n.startswith("lib/")}),
        "signing": sorted(n for n in names if n.startswith("META-INF/") and n.endswith((".RSA", ".DSA", ".EC"))),
    }


report = {apk.stem: inspect(apk) for apk in sorted(FIXTURES.glob("*.apk")) if apk.stem != "MITAI2Companion"}
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
