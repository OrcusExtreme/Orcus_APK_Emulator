"""Preflight (ARCHITECTURE.md §7.4): what the PC phone cannot do, before installing. Pure function."""

from orcus.contracts import ApkInfo, DeviceCaps, ErrorCode, Issue, IssueCode, IssueLevel

from .inspect import LANDSCAPE

DEVICE_SDK = 36
DEVICE_ABIS = ("x86_64", "arm64-v8a")   # google_apis 36 translates arm64 only (P)
SCAN_ACTION = "com.google.zxing.client.android.SCAN"
RECOGNITION_SERVICE = "android.speech.RecognitionService"
LOCATION_PERMS = frozenset({"android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"})


def preflight(info: ApkInfo, caps: DeviceCaps | None) -> list[Issue]:
    """caps None (engine not up yet) checks against the pinned image instead."""
    issues: list[Issue] = []
    api = caps.api if caps else DEVICE_SDK
    abis = set(caps.abis if caps else DEVICE_ABIS)
    if info.min_sdk > api:
        issues.append(Issue(IssueLevel.BLOCK, ErrorCode.INSTALL_OLDER_SDK, "error.install_older_sdk"))
    if info.native_abis and not info.native_abis & abis:
        issues.append(Issue(IssueLevel.BLOCK, ErrorCode.INSTALL_NO_ABI, "error.install_no_abi"))
    if "android.hardware.nfc" in info.features or "android.permission.NFC" in info.permissions:
        issues.append(Issue(IssueLevel.WARN, IssueCode.HW_NFC, "issue.hw_nfc"))
    if any(p.startswith("android.permission.BLUETOOTH") for p in info.permissions):
        issues.append(Issue(IssueLevel.WARN, IssueCode.HW_BLUETOOTH, "issue.hw_bluetooth"))
    if "android.hardware.usb.host" in info.features:
        issues.append(Issue(IssueLevel.WARN, IssueCode.HW_SERIAL, "issue.hw_serial"))
    if "android.permission.RECORD_AUDIO" in info.permissions:
        issues.append(Issue(IssueLevel.WARN, IssueCode.NEEDS_MIC, "issue.needs_mic"))
    if SCAN_ACTION in info.queries:
        issues.append(Issue(IssueLevel.WARN, IssueCode.SCANNER, "issue.scanner"))
    if info.target_sdk < 24:
        key = "issue.legacy_review" if info.target_sdk <= 22 else "issue.legacy"
        issues.append(Issue(IssueLevel.WARN, IssueCode.LEGACY, key))
    if info.native_abis & abis and not info.native_abis & {"x86_64", "x86"}:
        issues.append(Issue(IssueLevel.WARN, IssueCode.ARM_TRANSLATION, "issue.arm_translation"))
    if info.launcher_orientation in LANDSCAPE:
        issues.append(Issue(IssueLevel.WARN, IssueCode.ORIENTATION_LOCKED, "issue.orientation_locked"))
    if "android.permission.VIBRATE" in info.permissions:
        issues.append(Issue(IssueLevel.INFO, IssueCode.VIBRATE, "issue.vibrate"))
    if info.permissions & LOCATION_PERMS:
        issues.append(Issue(IssueLevel.INFO, IssueCode.LOCATION, "issue.location"))
    # TTS cannot be read from the manifest (every AI2 APK carries every component class, P): device facts only
    if caps is not None and caps.tts_engine is None:
        issues.append(Issue(IssueLevel.INFO, IssueCode.TTS_STATUS, "issue.tts_missing"))
    if caps is not None and not caps.speech_recognizer and RECOGNITION_SERVICE in info.queries:
        issues.append(Issue(IssueLevel.INFO, IssueCode.SPEECH_STATUS, "issue.speech_missing"))
    return issues


def blocking(issues: list[Issue]) -> Issue | None:
    return next((i for i in issues if i.level == IssueLevel.BLOCK), None)
