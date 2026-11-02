"""Manifest facts (ARCHITECTURE.md §7.3) with apkInspector 1.3.7's `get_manifest`.

apkInspector returns the decoded manifest as XML text; values come back as decoded strings and enum attributes
as numbers (screenOrientation "-1" / "0" in the fixtures). Parsing failures never block a run: the caller
falls back to `pm list packages` and `resolve-activity` after installing.

The file is untrusted: entries are inflated a chunk at a time and only up to a cap, and only the methods Android
reads (stored, deflated). A zip bomb's manifest, asset or icon never gets inflated in full; apkInspector's own
`parse_apk_for_manifest` reads the whole APK into memory and inflates without a limit.
"""

import io
import logging
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_root = logging.getLogger()
_root_before = (list(_root.handlers), _root.level)
from apkInspector.axml import get_manifest  # noqa: E402

# apkInspector 1.3.7 calls logging.basicConfig(level=DEBUG) when imported: every library's debug lines would go
# to the console. Put the root logger back.
_root.handlers[:] = _root_before[0]
_root.setLevel(_root_before[1])

from orcus.common import log  # noqa: E402
from orcus.contracts import ApkInfo

from .eras import ai_era

_log = log.get("apk.inspect")
A = "{http://schemas.android.com/apk/res/android}"
ORIENTATION = {"-1": "unspecified", "0": "landscape", "1": "portrait", "2": "user", "3": "behind", "4": "sensor",
               "5": "nosensor", "6": "sensorLandscape", "7": "sensorPortrait", "8": "reverseLandscape",
               "9": "reversePortrait", "10": "fullSensor", "11": "userLandscape", "12": "userPortrait",
               "13": "fullUser", "14": "locked"}
LANDSCAPE = frozenset({"landscape", "sensorLandscape", "reverseLandscape", "userLandscape"})
PORTRAIT = frozenset({"portrait", "sensorPortrait", "reversePortrait", "userPortrait"})
ICON_CANDIDATES = ("res/mipmap-xxxhdpi-v4/ic_launcher.png", "res/mipmap-xxhdpi-v4/ic_launcher.png",
                   "res/mipmap-xhdpi-v4/ic_launcher.png", "res/drawable/ya.png")
MANIFEST_MAX = 4 << 20   # App Inventor manifests are a few KB
ICON_MAX = 1 << 20
READABLE = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)   # what Android reads; bzip2/LZMA cannot be bounded
LIB_SO = re.compile(r"^lib/([^/]+)/[^/]+\.so$")          # only these make an ABI (not a stray lib/x/notes.txt)


def _entry(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """At most `limit` bytes of an entry, inflated a chunk at a time. Raises ValueError beyond that."""
    if info.compress_type not in READABLE:
        raise ValueError(f"{info.filename}: compression method {info.compress_type}")
    with z.open(info) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{info.filename}: more than {limit} bytes")
    return data


def _is_elf(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.file_size < 4 or info.compress_type not in READABLE:
        return False
    with z.open(info) as f:
        return f.read(4) == b"\x7fELF"


def orientation_name(value: str | None) -> str | None:
    if value is None:
        return None
    return ORIENTATION.get(value.strip(), value.strip())


def _xml(apk: Path) -> ET.Element:
    with zipfile.ZipFile(apk) as z:
        raw = _entry(z, z.getinfo("AndroidManifest.xml"), MANIFEST_MAX)
    text = get_manifest(io.BytesIO(raw))
    if not isinstance(text, str):   # a dict when it could not decode the manifest
        raise ValueError(f"manifest not decoded ({type(text).__name__})")
    # apkInspector may leave namespace declarations out; bind android: so ElementTree can read it
    if "xmlns:android" not in text[:2000]:
        text = re.sub(r"<manifest\b", '<manifest xmlns:android="http://schemas.android.com/apk/res/android"', text,
                      count=1)
    return ET.fromstring(text)


def _launcher(app: ET.Element, package: str) -> tuple[str | None, str | None]:
    """(launcher class, its screenOrientation). Relative names are resolved against the package."""
    for activity in [*app.iter("activity"), *app.iter("activity-alias")]:
        for f in activity.iter("intent-filter"):
            actions = {a.get(A + "name") for a in f.iter("action")}
            cats = {c.get(A + "name") for c in f.iter("category")}
            if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
                name = activity.get(A + "targetActivity") or activity.get(A + "name") or ""
                full = package + name if name.startswith(".") else name
                return full or None, orientation_name(activity.get(A + "screenOrientation"))
    return None, None


def inspect(apk: Path, sha256: str) -> ApkInfo:
    """Raises on unreadable manifests (the caller keeps going without facts)."""
    root = _xml(apk)
    package = root.get("package") or ""
    sdk = root.find("uses-sdk")
    min_sdk = int(sdk.get(A + "minSdkVersion", "1")) if sdk is not None else 1
    target = int(sdk.get(A + "targetSdkVersion", str(min_sdk))) if sdk is not None else min_sdk
    app = root.find("application")
    app = app if app is not None else ET.Element("application")
    launcher, orientation = _launcher(app, package)
    app_class = app.get(A + "name") or ""
    with zipfile.ZipFile(apk) as z:
        names = z.namelist()
        abis = frozenset(m.group(1) for n in names if (m := LIB_SO.match(n)))
        elf = any(_is_elf(z, info) for info in z.infolist() if info.filename.startswith("assets/"))
        icon = None
        for n in ICON_CANDIDATES:
            if n in names:
                try:
                    icon = _entry(z, z.getinfo(n), ICON_MAX)
                except ValueError:
                    pass
                break
    return ApkInfo(
        sha256=sha256, staged=apk, package=package, launcher=launcher,
        label=app.get(A + "label") or package.rsplit(".", 1)[-1],
        launcher_orientation=orientation,
        version_code=int(root.get(A + "versionCode", "0") or 0), version_name=root.get(A + "versionName"),
        min_sdk=min_sdk, target_sdk=target,
        permissions=frozenset(p.get(A + "name") or "" for p in root.iter("uses-permission")) - {""},
        queries=frozenset(a.get(A + "name") or "" for q in root.iter("queries") for a in q.iter("action")) - {""},
        features=frozenset(f.get(A + "name") or "" for f in root.iter("uses-feature")) - {""},
        native_abis=abis, asset_elf=elf,
        is_app_inventor=package.startswith("appinventor.ai_") or app_class.endswith("MultiDexApplication"),
        is_companion=app_class.endswith("ReplApplication"),
        ai_era=ai_era(target), icon_png=icon,
    )
