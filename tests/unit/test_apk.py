"""orcus.apk pure parts: intake, manifest, preflight, install table, launch commands, watcher transcripts (§7)."""

import zipfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import REPO
from orcus.apk import compat, eras, install, intake, launch, watcher
from orcus.apk import inspect as inspect_mod
from orcus.apk.inspect import inspect
from orcus.common import messages_ko
from orcus.contracts import (
    ApkInfo,
    CmdResult,
    DeviceCaps,
    ErrorCode,
    FileKind,
    GpuMode,
    IssueCode,
    IssueLevel,
    LogEntry,
    OrcusFailure,
    RemedyAction,
    Rotation,
)

FIXTURES = Path(r"D:\OrcusDev\fixtures")
PKG = "appinventor.ai_test.OrcusProbe"
PID = 4321
SYSTEM = 512
CAPS = DeviceCaps(api=36, abis=("x86_64", "arm64-v8a"), panel=(720, 1280, 320), renderer="ANGLE",
                  gpu_mode=GpuMode.HOST, hypervisor="WHPX", locale="ko-KR", timezone="Asia/Seoul",
                  tts_engine="com.google.android.tts", tts_ko=True, speech_recognizer=True, imes=())
NO_SPEECH = replace(CAPS, speech_recognizer=False)
NO_TTS = replace(CAPS, tts_engine=None, tts_ko=None)


# --- intake ----------------------------------------------------------------------------------------------------
def _zip(path: Path, names: Sequence[str]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    return path


@pytest.mark.parametrize("names,kind", [
    (["AndroidManifest.xml", "classes.dex"], FileKind.APK),
    (["BundleConfig.pb", "base/manifest/AndroidManifest.xml"], FileKind.AAB),
    (["base/manifest/AndroidManifest.xml"], FileKind.AAB),
    (["youngandroidproject/project.properties", "src/x/Screen1.scm"], FileKind.AIA),
    (["com.example.ext/components.json"], FileKind.AIX),
    (["readme.txt"], FileKind.NOT_ANDROID),
])
def test_classify_by_content_not_extension(tmp_path: Path, names: list[str], kind: FileKind) -> None:
    assert intake.classify(_zip(tmp_path / "renamed.apk", names)) is kind


def test_classify_broken_and_non_zip_and_real_aia(tmp_path: Path) -> None:
    good = _zip(tmp_path / "a.apk", ["AndroidManifest.xml"])
    (tmp_path / "cut.apk").write_bytes(good.read_bytes()[:40])
    (tmp_path / "text.apk").write_text("not a zip")
    assert intake.classify(tmp_path / "cut.apk") is FileKind.NOT_ANDROID
    assert intake.classify(tmp_path / "text.apk") is FileKind.NOT_ANDROID
    assert intake.classify(tmp_path / "missing.apk") is FileKind.NOT_ANDROID
    assert intake.classify(REPO / "tests" / "fixtures" / "OrcusProbe.aia") is FileKind.AIA
    assert intake.kind_error(FileKind.AIA) is ErrorCode.FILE_IS_AIA and intake.kind_error(FileKind.APK) is None


def test_stage_names_by_sha_from_a_korean_path_and_prunes(tmp_path: Path) -> None:
    src_dir = tmp_path / "내 드라이브" / "앱"
    src_dir.mkdir(parents=True)
    staging = tmp_path / "staging"
    shas = []
    for i in range(4):
        src = _zip(src_dir / f"고양이{i}.apk", ["AndroidManifest.xml", f"f{i}"])
        sha, staged = intake.stage(src, staging, keep=3)
        shas.append(sha)
        assert staged == staging / f"{sha}.apk" and staged.read_bytes() == src.read_bytes()
        assert staged.name.isascii()
    assert len(list(staging.glob("*.apk"))) == 3 and not list(staging.glob("*.part"))
    again, _ = intake.stage(src_dir / "고양이3.apk", staging, keep=3)
    assert again == shas[3]


def test_stage_keeps_what_it_just_staged_and_leaves_no_temp_file(tmp_path: Path) -> None:
    src = _zip(tmp_path / "a.apk", ["AndroidManifest.xml"])
    _, staged = intake.stage(src, tmp_path / "staging", keep=0)   # a bad staging_keep (3a review)
    assert staged.exists() and not list((tmp_path / "staging").glob(".incoming-*"))


def test_a_zip_claiming_too_many_entries_is_not_android(tmp_path: Path) -> None:
    """zipfile would build an object per claimed entry: millions of tiny ones exhaust memory (3a review)."""
    z = _zip(tmp_path / "a.apk", ["AndroidManifest.xml", "classes.dex"])
    assert intake.entry_count(z) == 2 and intake.classify(z) is FileKind.APK
    raw = bytearray(z.read_bytes())
    i = raw.rfind(b"PK\x05\x06")
    raw[i + 8:i + 12] = (60_000).to_bytes(2, "little") * 2
    z.write_bytes(bytes(raw))
    assert intake.entry_count(z) == 60_000 and intake.classify(z) is FileKind.NOT_ANDROID
    assert intake.entry_count(tmp_path / "missing.apk") is None


def test_a_zip_bomb_manifest_is_not_inflated_in_full(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.apk"
    with zipfile.ZipFile(bomb, "w") as z:   # 4 MiB of zeros deflate to a few KB
        z.writestr("AndroidManifest.xml", b"\0" * (inspect_mod.MANIFEST_MAX + 1), zipfile.ZIP_DEFLATED)
    with pytest.raises(ValueError):
        inspect(bomb, "sha")


@pytest.mark.skipif(not (FIXTURES / "HelloPurr.apk").exists(), reason="needs D:\\OrcusDev\\fixtures\\HelloPurr.apk")
def test_inspect_reads_only_bounded_entries_and_real_abis(tmp_path: Path) -> None:
    """bzip2/LZMA entries are never inflated (Android reads only stored/deflated); only lib/<abi>/*.so is an ABI."""
    with zipfile.ZipFile(FIXTURES / "HelloPurr.apk") as src:
        manifest = src.read("AndroidManifest.xml")
    apk = tmp_path / "x.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("AndroidManifest.xml", manifest, zipfile.ZIP_DEFLATED)
        z.writestr("assets/x", b"\x7fELF" + bytes(60), zipfile.ZIP_BZIP2)
        z.writestr("lib/foo/notes.txt", b"x")
        z.writestr("lib/arm64-v8a/libx.so", b"\x7fELF")
    info = inspect(apk, "sha")
    assert not info.asset_elf and info.native_abis == frozenset({"arm64-v8a"})


def test_stage_copy_failure_is_an_orcus_failure(tmp_path: Path) -> None:
    with pytest.raises(OrcusFailure) as e:
        intake.stage(tmp_path / "gone.apk", tmp_path / "staging")
    assert e.value.error.code is ErrorCode.STAGE_COPY_FAILED


# --- manifest --------------------------------------------------------------------------------------------------
def test_era_table() -> None:
    assert eras.ai_era(36) == "nb206+" and eras.ai_era(37) == "nb206+"
    assert eras.ai_era(33) == "nb194–197" and eras.ai_era(26) == "nb172–177"
    assert eras.ai_era(32) is None and eras.ai_era(22) == "2018년 12월 이전 빌드"


@pytest.mark.skipif(not (FIXTURES / "OrcusProbe.apk").exists(), reason="fixtures live in D:\\OrcusDev\\fixtures")
@pytest.mark.parametrize("name", ["HelloPurr", "TalkToMe", "OrcusProbe"])
def test_inspect_real_app_inventor_builds(name: str) -> None:
    info = inspect(FIXTURES / f"{name}.apk", "sha")
    assert info.package.startswith("appinventor.ai_") and info.package.endswith(f".{name}")
    assert info.launcher == f"{info.package}.Screen1" and info.launcher_orientation == "unspecified"
    assert (info.min_sdk, info.target_sdk, info.ai_era) == (14, 36, "nb206+")
    assert info.is_app_inventor and not info.is_companion and info.icon_png
    assert not info.native_abis
    if name == "OrcusProbe":
        assert compat.SCAN_ACTION in info.queries and compat.RECOGNITION_SERVICE in info.queries
        assert "android.permission.RECORD_AUDIO" in info.permissions


@pytest.mark.skipif(not (FIXTURES / "MITAI2Companion.apk").exists(), reason="fetched by tools/fetch_fixtures.py")
def test_inspect_companion_regression() -> None:
    info = inspect(FIXTURES / "MITAI2Companion.apk", "sha")
    assert info.is_companion and info.version_name == "2.81u"
    assert {"x86_64", "arm64-v8a", "armeabi-v7a"} <= info.native_abis
    assert len(info.permissions) > 30


# --- preflight -------------------------------------------------------------------------------------------------
def app(**kw: object) -> ApkInfo:
    base = ApkInfo(sha256="s", staged=Path("s.apk"), package=PKG, launcher=f"{PKG}.Screen1", label="Probe",
                   launcher_orientation="unspecified", version_code=1, version_name="1.0", min_sdk=14,
                   target_sdk=36, permissions=frozenset(), queries=frozenset(), features=frozenset(),
                   native_abis=frozenset(), asset_elf=False, is_app_inventor=True, is_companion=False,
                   ai_era="nb206+", icon_png=None)
    return replace(base, **kw)  # type: ignore[arg-type]


def codes(info: ApkInfo, caps: DeviceCaps | None = CAPS) -> dict[object, IssueLevel]:
    return {i.code: i.level for i in compat.preflight(info, caps)}


def test_plain_app_has_no_issues() -> None:
    assert compat.preflight(app(), CAPS) == [] and compat.preflight(app(), None) == []


@pytest.mark.parametrize("kw,code,level", [
    ({"min_sdk": 37}, ErrorCode.INSTALL_OLDER_SDK, IssueLevel.BLOCK),
    ({"native_abis": frozenset({"armeabi-v7a"})}, ErrorCode.INSTALL_NO_ABI, IssueLevel.BLOCK),
    ({"native_abis": frozenset({"arm64-v8a"})}, IssueCode.ARM_TRANSLATION, IssueLevel.WARN),
    ({"permissions": frozenset({"android.permission.NFC"})}, IssueCode.HW_NFC, IssueLevel.WARN),
    ({"permissions": frozenset({"android.permission.BLUETOOTH_CONNECT"})}, IssueCode.HW_BLUETOOTH, IssueLevel.WARN),
    ({"features": frozenset({"android.hardware.usb.host"})}, IssueCode.HW_SERIAL, IssueLevel.WARN),
    ({"permissions": frozenset({"android.permission.RECORD_AUDIO"})}, IssueCode.NEEDS_MIC, IssueLevel.WARN),
    ({"queries": frozenset({compat.SCAN_ACTION})}, IssueCode.SCANNER, IssueLevel.WARN),
    ({"target_sdk": 23}, IssueCode.LEGACY, IssueLevel.WARN),
    ({"launcher_orientation": "landscape"}, IssueCode.ORIENTATION_LOCKED, IssueLevel.WARN),
    ({"launcher_orientation": "sensorLandscape"}, IssueCode.ORIENTATION_LOCKED, IssueLevel.WARN),
    ({"permissions": frozenset({"android.permission.VIBRATE"})}, IssueCode.VIBRATE, IssueLevel.INFO),
    ({"permissions": frozenset({"android.permission.ACCESS_COARSE_LOCATION"})}, IssueCode.LOCATION, IssueLevel.INFO),
])
def test_preflight_matrix(kw: dict[str, object], code: object, level: IssueLevel) -> None:
    assert codes(app(**kw)) == {code: level}


def test_preflight_device_facts_and_edges() -> None:
    assert codes(app(native_abis=frozenset({"x86_64", "arm64-v8a", "armeabi-v7a"}))) == {}
    assert codes(app(launcher_orientation="portrait")) == {}
    assert codes(app(), NO_TTS) == {IssueCode.TTS_STATUS: IssueLevel.INFO}
    assert codes(app(), NO_SPEECH) == {}   # only apps that look for a recognizer get the chip
    speech = app(queries=frozenset({compat.RECOGNITION_SERVICE}))
    assert codes(speech, NO_SPEECH) == {IssueCode.SPEECH_STATUS: IssueLevel.INFO}
    assert codes(app(min_sdk=37), replace(CAPS, api=37)) == {}
    keys = {i.text_key for i in compat.preflight(app(target_sdk=22), CAPS)}
    assert keys == {"issue.legacy_review"}
    block = compat.blocking(compat.preflight(app(min_sdk=40, permissions=frozenset({"android.permission.NFC"})),
                                             CAPS))
    assert block is not None and block.code is ErrorCode.INSTALL_OLDER_SDK


def test_every_issue_text_resolves() -> None:
    everything = app(min_sdk=40, native_abis=frozenset({"armeabi-v7a"}), target_sdk=22,
                     launcher_orientation="landscape",
                     queries=frozenset({compat.SCAN_ACTION, compat.RECOGNITION_SERVICE}),
                     features=frozenset({"android.hardware.usb.host", "android.hardware.nfc"}),
                     permissions=frozenset({"android.permission.BLUETOOTH", "android.permission.RECORD_AUDIO",
                                            "android.permission.VIBRATE", "android.permission.ACCESS_FINE_LOCATION"}))
    issues = compat.preflight(everything, replace(NO_TTS, speech_recognizer=False))
    issues += compat.preflight(app(target_sdk=23, native_abis=frozenset({"arm64-v8a"})), CAPS)
    assert {i.code for i in issues} >= set(IssueCode)
    for i in issues:
        assert messages_ko.text(i.text_key)


# --- shells ----------------------------------------------------------------------------------------------------
class ScriptShell:
    serial = "emulator-5640"

    def __init__(self, handler: Callable[[tuple[str, ...]], tuple[int, str]] = lambda a: (0, "")) -> None:
        self.epoch = 1
        self.handler = handler
        self.calls: list[tuple[str, ...]] = []
        self.install_flags: list[list[str]] = []
        self.install_results: list[tuple[int, str, bool]] = []   # (code, output, timed_out)
        self.state = "device"

    def get_state(self) -> str:
        return self.state

    def shell(self, argv: Sequence[str], timeout_s: float = 30) -> CmdResult:
        a = tuple(argv)
        self.calls.append(a)
        code, out = self.handler(a)
        return CmdResult(a, code, out, "", 0.01, False)

    def install(self, apk: Path, flags: Sequence[str], timeout_s: float = 180) -> CmdResult:
        self.install_flags.append(list(flags))
        code, out, timed_out = self.install_results.pop(0)
        return CmdResult(("install",), code, out, "", 1.0, timed_out)

    def uninstall(self, package: str) -> CmdResult:
        return CmdResult(("uninstall", package), 0, "Success", "", 0.1, False)


OK = (0, "Performing Streamed Install\nSuccess", False)


def test_install_flags() -> None:
    assert install.flags(grant_all=False, target_sdk=36) == ["-r"]
    assert install.flags(grant_all=True, target_sdk=23) == ["-r", "-g", install.BYPASS_LOW_TARGET]


@pytest.mark.parametrize("first,second_flag", [
    ("Failure [INSTALL_FAILED_DEPRECATED_SDK_VERSION: App package must target at least SDK version 24]",
     install.BYPASS_LOW_TARGET),
    ("Failure [INSTALL_FAILED_VERSION_DOWNGRADE: Downgrade detected]", "-d"),
])
def test_install_retries_itself(first: str, second_flag: str) -> None:
    sh = ScriptShell()
    sh.install_results = [(1, first, False), OK]
    out = install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36)
    assert out.ok and out.seconds == 2.0
    assert sh.install_flags == [["-r"], ["-r", second_flag]]


@pytest.mark.parametrize("text,code", [
    ("Failure [INSTALL_FAILED_NO_MATCHING_ABIS: Failed to extract native libraries, res=-113]",
     ErrorCode.INSTALL_NO_ABI),
    ("Failure [INSTALL_FAILED_OLDER_SDK: Requires newer sdk version #40 (current version is 36)]",
     ErrorCode.INSTALL_OLDER_SDK),
    ("Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]", ErrorCode.INSTALL_STORAGE),
    ("Failure [INSTALL_PARSE_FAILED_NOT_APK: Failed to parse]", ErrorCode.INSTALL_INVALID),
    ("adb: failed to install x.apk: Failure [INVALID_APK]", ErrorCode.INSTALL_INVALID),
    ("Failure [INSTALL_FAILED_SOMETHING_NEW]", ErrorCode.INSTALL_OTHER),
])
def test_install_failure_table(text: str, code: ErrorCode) -> None:
    sh = ScriptShell()
    sh.install_results = [(1, text, False)]
    out = install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36)
    assert not out.ok and out.code is code and text[-40:] in out.detail


def test_install_incompatible_asks_and_offline_retries_once() -> None:
    sh = ScriptShell()
    msg = (f"Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package {PKG} signatures do not match "
           "newer version; ignoring!]")
    sh.install_results = [(1, msg, False)]
    out = install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36)
    assert out.needs_uninstall_prompt and out.code is None
    assert install.incompatible_package(out.detail) == PKG
    sh.install_results = [(1, "", True), OK]
    assert install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36).ok
    sh.install_results = [(1, "adb: device offline", False)]
    sh.state = "offline"
    assert install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36).code is ErrorCode.ENGINE_ADB_LOST


def test_install_failure_is_read_from_its_code_only() -> None:
    """adb's output can echo manifest text: a crafted sharedUserId must not fake 'replace?' (3a review)."""
    sh = ScriptShell()
    sh.install_results = [(1, "Failure [INSTALL_PARSE_FAILED_BAD_SHARED_USER_ID: bad sharedUserId x "
                              "INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package com.victim signatures]", False)]
    assert not install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36).needs_uninstall_prompt
    sh.install_results = [(1, "", True), (1, "", True)]   # timed out, the device answers, timed out again
    assert install.install(sh, Path("a.apk"), grant_all=False, target_sdk=36).code is ErrorCode.ENGINE_ADB_LOST


def test_package_from_the_install_difference() -> None:
    before = frozenset({f"package:/data/app/~~a==/{PKG}-b==/base.apk={PKG}", "package:/data/app/~~c/x-d/base.apk=x"})
    after_new = before | {"package:/data/app/~~e/y.Foo-f/base.apk=y.Foo"}
    after_reinstall = (before - {f"package:/data/app/~~a==/{PKG}-b==/base.apk={PKG}"}) | {
        f"package:/data/app/~~g==/{PKG}-h==/base.apk={PKG}"}
    assert install.installed_package(before, after_new) == "y.Foo"
    assert install.installed_package(before, after_reinstall) == PKG
    assert install.installed_package(before, before) is None
    sh = ScriptShell(lambda a: (0, "package:/data/app/x/base.apk=x\nnoise\n"))
    assert install.third_party(sh) == {"package:/data/app/x/base.apk=x"}


# --- launch ----------------------------------------------------------------------------------------------------
R = Rotation


@pytest.mark.parametrize("orientation,current,want", [
    ("unspecified", R.PORTRAIT, None), (None, R.LANDSCAPE, None),
    ("landscape", R.PORTRAIT, R.LANDSCAPE), ("landscape", R.LANDSCAPE, None),
    ("landscape", R.REVERSE_LANDSCAPE, R.LANDSCAPE),        # a "landscape" lock is drawn at ROTATION_90 (S7)
    ("reverseLandscape", R.PORTRAIT, R.REVERSE_LANDSCAPE),
    ("sensorLandscape", R.PORTRAIT, R.LANDSCAPE), ("sensorLandscape", R.REVERSE_LANDSCAPE, None),
    ("userLandscape", R.REVERSE_PORTRAIT, R.LANDSCAPE),
    ("portrait", R.LANDSCAPE, R.PORTRAIT), ("portrait", R.PORTRAIT, None),
    ("sensorPortrait", R.REVERSE_LANDSCAPE, R.PORTRAIT), ("sensorPortrait", R.REVERSE_PORTRAIT, None),
])
def test_pre_rotation(orientation: str | None, current: Rotation, want: Rotation | None) -> None:
    assert launch.wanted_rotation(orientation, current) is want


def test_display_rotation_reads_dumpsys() -> None:
    sh = ScriptShell(lambda a: (0, "Display: mDisplayId=0\n  mCurrentRotation=ROTATION_270 mLastOrientation=0\n"))
    assert launch.display_rotation(sh) is Rotation.REVERSE_LANDSCAPE
    assert sh.calls == [("dumpsys", "window", "displays")]
    assert launch.display_rotation(ScriptShell(lambda a: (0, ""))) is None


def phone(*, activities: set[str], pid: int | None = PID) -> Callable[[tuple[str, ...]], tuple[int, str]]:
    def handler(a: tuple[str, ...]) -> tuple[int, str]:
        match a:
            case ("am", "start", "-W", "-n", comp):
                if comp in activities:
                    return 0, f"Starting: Intent {{ cmp={comp} }}\nStatus: ok\nLaunchState: COLD\nTotalTime: 812\n"
                return 0, "Error type 3\nError: Activity class does not exist."
            case ("cmd", "package", "resolve-activity", *_rest):
                found = next(iter(activities), None)
                return 0, f"priority=0 preferredOrder=0\n{found}\n" if found else "No activity found\n"
            case ("pidof", _pkg):
                return (0, f"{pid}\n") if pid else (1, "")
            case _:
                return 0, ""
    return handler


def test_launch_ok_and_resolve_fallback() -> None:
    sh = ScriptShell(phone(activities={f"{PKG}/{PKG}.Screen1"}))
    out = launch.launch(sh, PKG, f"{PKG}.Screen1")
    assert (out.ok, out.pid, out.total_ms) == (True, PID, 812)
    sh = ScriptShell(phone(activities={f"{PKG}/.Screen1"}))
    out = launch.launch(sh, PKG, f"{PKG}.Renamed")
    assert out.ok and out.component == f"{PKG}/.Screen1"
    out = launch.launch(sh, PKG, None)    # unreadable manifest: resolve-activity first
    assert out.ok and sh.calls[-3][0:3] == ("cmd", "package", "resolve-activity")


def test_launch_not_found_and_no_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    out = launch.launch(ScriptShell(phone(activities=set())), PKG, f"{PKG}.Screen1")
    assert not out.ok and out.code is ErrorCode.APP_LAUNCH_NOT_FOUND
    monkeypatch.setattr(launch, "PID_WAIT_S", 0.0)
    slept: list[float] = []
    out = launch.launch(ScriptShell(phone(activities={f"{PKG}/{PKG}.Screen1"}, pid=None)), PKG, f"{PKG}.Screen1",
                        sleep=slept.append)
    assert out.ok and out.pid is None and not slept


def test_grant_all_skips_unchangeable() -> None:
    sh = ScriptShell(lambda a: (0, "") if a[-1].endswith("LOCATION") else (255, "not a changeable permission"))
    perms = frozenset({"android.permission.ACCESS_FINE_LOCATION", "android.permission.INTERNET"})
    assert launch.grant_all(sh, PKG, perms) == 1
    assert ("pm", "grant", PKG, "android.permission.INTERNET") in sh.calls


# --- watcher ---------------------------------------------------------------------------------------------------
T0 = 1_761_800_000_000_000


def E(tag: str, msg: str, *, t: float = 0.0, pid: int = PID, level: str = "E") -> LogEntry:
    return LogEntry(T0 + int(t * 1_000_000), pid, pid, level, tag, msg)


def run(lines: list[LogEntry], caps: DeviceCaps | None = CAPS, *, flush: bool = True) -> list[watcher.Finding]:
    w = watcher.Watcher(PKG, PID, caps)
    out = [f for e in lines for f in w.feed(e)]
    return out + (w.flush() if flush else [])


def died(t: float) -> LogEntry:
    return E("ActivityManager", f"Process {PKG} (pid {PID}) has died: fg  TOP ", t=t, pid=SYSTEM, level="I")


def fatal(*cause: str, t: float = 0.0) -> list[LogEntry]:
    head = ["FATAL EXCEPTION: main", f"Process: {PKG}, PID: {PID}", *cause, "\tat android.os.Looper.loop(Looper.java:1)"]
    return [E("AndroidRuntime", m, t=t + i * 0.001) for i, m in enumerate(head)]


def test_crash_from_am_crash_becomes_one_card() -> None:
    lines = [*fatal("android.app.RemoteServiceException$CrashedByAdbException: shell-induced crash"),
             E("ActivityTaskManager", "Force finishing activity", t=0.01, pid=SYSTEM, level="W"), died(0.2)]
    [f] = run(lines)
    assert f.ended and f.error.code is ErrorCode.APP_CRASH
    assert f.error.detail == "android.app.RemoteServiceException$CrashedByAdbException: shell-induced crash"
    assert "FATAL EXCEPTION" in f.error.log_excerpt
    assert f.error.remedy is not None and f.error.remedy.action is RemedyAction.RUN_AGAIN


def test_crash_held_through_other_processes_until_its_stack_ends() -> None:
    lines = [*fatal("java.lang.NullPointerException: boom")[:2],
             E("SurfaceFlinger", "noise", t=0.002, pid=SYSTEM, level="I"),
             *fatal("java.lang.NullPointerException: boom")[2:]]
    w = watcher.Watcher(PKG, PID, CAPS)
    assert [f for e in lines for f in w.feed(e)] == []
    [f] = w.feed(E("Process", f"Sending signal. PID: {PID} SIG: 9", t=0.01, level="I"))
    assert f.error.code is ErrorCode.APP_CRASH and f.error.detail == "java.lang.NullPointerException: boom"
    assert w.feed(died(0.3)) == [] and w.ended


def test_crash_closed_by_time_or_end_of_stream() -> None:
    [f] = run([*fatal("java.lang.IllegalStateException: x"), E("Other", "late", t=1.0, pid=SYSTEM)])
    assert f.error.code is ErrorCode.APP_CRASH
    [f] = run(fatal("java.lang.IllegalStateException: x"))   # flush() at the end
    assert f.ended


def test_native_crash_is_a_crash() -> None:
    lines = [E("chromium", "[FATAL:crashpad_client_linux.cc(745)] Render process (4989)'s crash wasn't handled by "
                           "all associated  webviews, triggering application crash.", level="F"),
             E("libc", f"Fatal signal 5 (SIGTRAP), code 128 (SI_KERNEL), fault addr 0x0 in tid {PID} (pkg), "
                       f"pid {PID} (pkg)", t=0.001, level="F"),
             died(0.3)]
    [f] = run(lines)
    assert f.ended and f.error.code is ErrorCode.APP_CRASH and f.error.detail.startswith("Fatal signal 5")
    assert "Render process" in f.error.log_excerpt


def test_speech_crash_is_speech_missing_not_crash() -> None:
    cause = ("android.content.ActivityNotFoundException: No Activity found to handle Intent "
             "{ act=android.speech.action.RECOGNIZE_SPEECH (has extras) }")
    [f] = run([*fatal("java.lang.RuntimeException: Failure delivering result", f"Caused by: {cause}"), died(0.3)])
    assert f.ended and f.error.code is ErrorCode.APP_SPEECH_MISSING
    assert f.error.message_key == "app.speech_missing" and messages_ko.text(f.error.message_key)


def test_speech_legacy_path_is_one_card() -> None:
    intent = "No Activity found to handle Intent { act=android.speech.action.RECOGNIZE_SPEECH (has extras) }"
    lines = [E("System.err", f"android.content.ActivityNotFoundException: {intent}", level="W"),
             E("System.err", "\tat android.app.Instrumentation.checkStartActivityResult", t=0.001, level="W"),
             E("RuntimeErrorAlert", f"alert({intent}, Runtime Error, End Application", t=0.05)]
    [f] = run(lines)
    assert f.error.code is ErrorCode.APP_SPEECH_MISSING and not f.ended
    plain_alert = [lines[0], E("RuntimeErrorAlert", "alert(Runtime Error, Runtime Error, End Application", t=0.1)]
    assert [f.error.code for f in run(plain_alert)] == [ErrorCode.APP_SPEECH_MISSING]


def test_speech_service_path() -> None:
    bind = E("SpeechRecognizer", "Bind to system recognition service failed with error 10")
    form = E("Form", "Form Screen1 ErrorOccurred, errorNumber = 0, componentType = SpeechRecognizer, "
                     "functionName = GetText, messages = Try Again.", t=0.02)
    # the bind failure is proof at run time, even when the caps cached with the golden still list a recognizer
    assert [f.error.code for f in run([bind, form], CAPS)] == [ErrorCode.APP_SPEECH_MISSING]
    assert [f.error.code for f in run([bind, form], NO_SPEECH)] == [ErrorCode.APP_SPEECH_MISSING]
    assert [f.error.code for f in run([form], NO_SPEECH)] == [ErrorCode.APP_SPEECH_MISSING]
    [f] = run([form], CAPS)
    assert f.error.code is ErrorCode.APP_AI_COMPONENT_ERROR and f.error.message_key == "app.error.0"


def test_runtime_error_alert_with_stack() -> None:
    msg = ("alert(Select list item: Attempt to get item number 5 of a list of length 0: [], "
           "Select list item: List index too large, End Application")
    lines = [E("System.err", "com.google.appinventor.components.runtime.errors.YailRuntimeError: x", level="W"),
             E("System.err", "\tat com.google.youngandroid.runtime.lambda", t=0.001, level="W"),
             E("RuntimeErrorAlert", msg, t=0.01)]
    [f] = run(lines)
    assert f.error.code is ErrorCode.APP_AI_RUNTIME_ERROR and not f.ended
    assert f.error.detail.endswith("List index too large") and "End Application" not in f.error.detail


@pytest.mark.parametrize("number,component,function,text,key", [
    (1501, "BarcodeScanner", "BarcodeScanner", "Your device does not have a scanning application installed.",
     "app.error.1501"),
    (601, "ActivityStarter", "StartActivity", "No corresponding activity was found.", "app.error.601"),
    (2701, "TextToSpeech", "TextToSpeech", "TextToSpeech is not yet ready to perform this operation",
     "app.error.2701"),
    (3806, "SpeechRecognizer", "GetText", "No Match", "app.error.3806"),
    (908, "SoundRecorder", "Start", "The permission RECORD_AUDIO has been denied. Please enable it in the "
     "Settings app.", "app.error.908"),
    (1103, "Web", "Get", "Unable to get a response with the specified URL: x", "error.app_ai_component_error"),
])
def test_component_errors(number: int, component: str, function: str, text: str, key: str) -> None:
    form = (f"Form Screen1 ErrorOccurred, errorNumber = {number}, componentType = {component}, "
            f"functionName = {function}, messages = {text}")
    toasts = [E("Toast", f"{m}() shouldn't be called on text toasts, the values won't be used", t=0.01, level="W")
              for m in ("getXOffset", "getYOffset", "setGravity")]
    scan = [E("System.err", "android.content.ActivityNotFoundException: No Activity found to handle Intent "
                            "{ act=com.google.zxing.client.android.SCAN }", t=-0.01, level="W")]
    [f] = run([*scan, E("Form", form), *toasts])
    assert f.error.code is ErrorCode.APP_AI_COMPONENT_ERROR and f.error.message_key == key
    assert f.error.detail == f"{number} · {component}.{function}: {text}"
    assert messages_ko.text(f.error.message_key)
    action = f.error.remedy.action if f.error.remedy else None
    assert action is (RemedyAction.GRANT_ALL if number == 908 else RemedyAction.OPEN_LOG)


def test_repeated_errors_collapse_for_three_seconds() -> None:
    form = ("Form Screen1 ErrorOccurred, errorNumber = 601, componentType = ActivityStarter, "
            "functionName = StartActivity, messages = No corresponding activity was found.")
    found = run([E("Form", form), E("Form", form, t=1.0), E("Form", form, t=4.5)])
    assert len(found) == 2


def test_tts_missing() -> None:
    retries = E("InternalTTS", "max number of speak retries exceeded (20): speak will fail", level="D")
    not_init = E("InternalTTS", "speak called when TTS not initialized", level="D")
    assert [f.error.code for f in run([retries, replace(retries, ts_us=retries.ts_us + 20_000_000)])] == [
        ErrorCode.APP_TTS_MISSING]
    assert run([not_init]) == []                       # normal while the engine starts
    [f] = run([not_init], NO_TTS)
    assert f.error.code is ErrorCode.APP_TTS_MISSING and f.error.message_key == "app.tts_missing"


def test_anr_from_window_manager_line() -> None:
    lines = [E("InputDispatcher", f"Window 5f2 {PKG}/{PKG}.Screen1 is unresponsive", pid=SYSTEM, level="W"),
             E("WindowManager", f"ANR in Window{{5f2 u0 {PKG}/{PKG}.Screen1}}. Reason:Input dispatching timed out",
               t=0.01, pid=SYSTEM, level="I"),
             E("ActivityManager", f"ANR in {PKG} ({PKG}/.Screen1)", t=0.5, pid=SYSTEM),
             E("ActivityManager", f"ANR in {PKG}2 ({PKG}2/.Screen1)", t=20, pid=SYSTEM)]
    [f] = run(lines)
    assert f.error.code is ErrorCode.APP_ANR and not f.ended


def test_died_without_crash_and_other_processes_ignored() -> None:
    other = [*(replace(e, pid=999) for e in fatal("java.lang.Error: not ours")),
             E("ActivityManager", f"Process {PKG} (pid 999) has died", t=0.1, pid=SYSTEM)]
    assert run(other) == []
    [f] = run([E("ActivityManager", "Start proc", pid=SYSTEM, level="I"), died(1.0),
               E("Form", "Form Screen1 ErrorOccurred, errorNumber = 601, componentType = A, functionName = B, "
                         "messages = C", t=1.1)])
    assert f.ended and f.error.code is ErrorCode.APP_DIED


def test_crash_buffer_when_no_pid_appeared() -> None:
    def tt(msg: str) -> str:
        return f"10-30 17:01:02.345  {PID}  {PID} E AndroidRuntime: {msg}"
    text = "\n".join([tt("FATAL EXCEPTION: main"), tt(f"Process: {PKG}, PID: {PID}"),
                      tt("java.lang.RuntimeException: Unable to start activity"), tt("\tat a.b(c.java:1)")])
    err = watcher.crash_from_buffer(text, PKG)
    assert err is not None and err.code is ErrorCode.APP_CRASH
    assert err.detail == "java.lang.RuntimeException: Unable to start activity"
    assert watcher.crash_from_buffer(text, "other.pkg") is None
    speech = text.replace("Unable to start activity",
                          "No Activity found to handle Intent { act=android.speech.action.RECOGNIZE_SPEECH }")
    err = watcher.crash_from_buffer(speech, PKG)
    assert err is not None and err.code is ErrorCode.APP_SPEECH_MISSING
