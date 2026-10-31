"""Engine building blocks: adb argv, exit-log classes, renderer check, provisioning steps, golden validity."""

from pathlib import Path

from _engine_fakes import FakeDevice, FastClock

from orcus.common.paths import OrcusPaths
from orcus.contracts import CmdResult, ErrorCode, GpuMode, SessionMode, Settings
from orcus.contracts.session import EngineSettings
from orcus.engine import adb, exitlog, gpu, provision, snapshots


def sh_for(dev: FakeDevice):   # type: ignore[no-untyped-def]
    def sh(cmd: str, timeout_s: float = 30) -> CmdResult:
        code, out = dev.shell(cmd)
        return CmdResult(("shell", cmd), code, out, "", 0.0, False)
    return sh


def test_adb_client_argv_never_lets_adb_start_a_server() -> None:
    assert adb.client(15037, "emulator-5640", "get-state") == ["-H", "127.0.0.1", "-P", "15037", "-s",
                                                                "emulator-5640", "get-state"]
    assert adb.client(15037, None, "version") == ["-H", "127.0.0.1", "-P", "15037", "version"]


class _Lines:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.closed = False

    def __iter__(self):   # type: ignore[no-untyped-def]
        yield from self.lines

    def close(self) -> None:
        self.closed = True


def test_framework_problem_and_cpu_busy() -> None:
    dev = FakeDevice()
    sh = sh_for(dev)
    assert provision.framework_problem(sh) is None
    dev.framework_up = False
    assert provision.framework_problem(sh) == "system_server is not running"
    dev.busy = 0.25
    busy = provision.cpu_busy(sh)
    assert busy is not None and abs(busy - 0.25) < 0.01
    assert provision.load_average(sh) == 0.4


def test_adb_logcat_parses_threadtime_and_ends_the_process_on_cancel() -> None:
    lines = _Lines(["--------- beginning of main",
                    "10-30 19:30:01.123  4242  4242 E Form    : Form Screen1 ErrorOccurred, errorNumber = 601",
                    "10-30 19:30:01.200  4242  4250 D InternalTTS: speak called when TTS not initialized"])
    stream = adb.AdbLogcat(lines)
    assert [(e.pid, e.tid, e.level, e.tag) for e in stream] == [(4242, 4242, "E", "Form"),
                                                               (4242, 4250, "D", "InternalTTS")]
    stream.cancel()
    assert lines.closed
    assert adb.AdbLogcat.ARGS == ("logcat", "-v", "threadtime", "-T", "1")   # from now on, not the backlog


def test_epoch_shell_quotes_and_guards() -> None:
    seen: list[list[str]] = []
    alive = {"epoch": 3}

    def run(argv, timeout_s):   # type: ignore[no-untyped-def]
        seen.append(list(argv))
        return CmdResult(tuple(argv), 0, "", "", 0.0, False)

    def guard(epoch: int) -> None:
        if epoch != alive["epoch"]:
            raise adb.not_ready("gone")
    s = adb.EpochShell(run, 15037, "emulator-5640", 3, guard)
    s.shell(["am", "start", "-n", "a b/.Screen1", ""])
    assert seen[-1][-2:] == ["shell", "am start -n 'a b/.Screen1' ''"]
    alive["epoch"] = 4
    try:
        s.get_state()
        raise AssertionError("old epoch must fail")
    except adb.EngineNotReady as e:
        assert e.error.code == ErrorCode.ENGINE_UNAVAILABLE


def test_exit_log_classes() -> None:
    assert exitlog.classify("PANIC: Broken AVD system path", golden=True).code == ErrorCode.PRE_SDK_MISSING
    assert exitlog.classify("x\nNot enough space to create userdata\n", golden=False).code == ErrorCode.PRE_DISK
    locked = exitlog.classify("Please use -read-only flag", golden=False)
    assert locked.code == ErrorCode.ENGINE_AVD_LOCKED and locked.retry
    assert exitlog.classify("ERROR | Failed to load snapshot 'orcus_golden'", golden=True).golden_load_failed
    assert exitlog.classify("ERROR | Failed to load snapshot 'orcus_golden'", golden=False).code == ErrorCode.ENGINE_EXIT
    assert exitlog.classify("x86_64 emulation currently requires hardware acceleration", golden=False).code \
        == ErrorCode.PRE_WHPX_OFF
    assert exitlog.classify("something else", golden=True).code == ErrorCode.ENGINE_EXIT


def test_renderer_check() -> None:
    line = "GLES: Google (NVIDIA Corporation), Android Emulator OpenGL ES Translator (NVIDIA GeForce GTX 1660)"
    assert gpu.renderer(line).startswith("Google (NVIDIA")
    assert not gpu.is_software(gpu.renderer(line))
    assert gpu.is_software("Google (Google Inc.), Android Emulator OpenGL ES Translator (Google SwiftShader)")
    assert gpu.renderer("") == ""


def test_apply_checked_retries_until_read_back_then_reports_failure() -> None:
    dev = FakeDevice()
    dev.never_persist.add(("global", "bluetooth_on"))
    out = provision.apply_checked(sh_for(dev), FastClock(), provision.PROVISION, timeout_s=3)
    assert out["settings put secure stylus_handwriting_enabled 0"] == "ok"
    assert out["cmd bluetooth_manager disable"].startswith("FAILED")
    assert sum(v == "ok" for v in out.values()) == len(provision.PROVISION) - 1


def test_flush_and_missing_after_power_off() -> None:
    dev = FakeDevice()
    sh = sh_for(dev)
    provision.apply_checked(sh, FastClock(), provision.PROVISION)
    dev.power_off()                       # without a flush the last writes are gone (Phase 2 finding)
    assert provision.missing(sh)
    provision.apply_checked(sh, FastClock(), provision.PROVISION)
    assert provision.flush(sh, FastClock())
    dev.power_off()
    assert provision.missing(sh) == []


def test_wait_framework_restart_sees_the_restart() -> None:
    dev = FakeDevice()
    dev.cold_boot(wipe=False, change_locale="ko-KR")
    waited = provision.wait_framework_restart(sh_for(dev), FastClock(), timeout_s=60)
    assert waited < 60
    assert dev.props["sys.system_server.start_count"] == "2"


def test_session_init_sets_nonce_location_and_ram_props() -> None:
    dev = FakeDevice()
    r = provision.session_init(sh_for(dev), nonce="abc123", lat=37.5665, lon=126.978, rerun_ram_props=True)
    assert r.nonce_ok and r.location_ok and r.failures == ()
    assert dev.props["debug.orcus.session"] == "abc123"
    assert dev.props["debug.wm.disable_deprecated_target_sdk_dialog"] == "true"
    cmds = "\n".join(dev.commands)
    assert "appops set com.android.shell android:mock_location allow" in cmds
    assert "set-test-provider-location gps --location 37.566500,126.978000 --accuracy 5" in cmds
    assert "set-test-provider-location fused" in cmds
    assert r.clock_skew_s is not None and abs(r.clock_skew_s) < 5 and not r.clock_fixed


def test_detect_caps() -> None:
    dev = FakeDevice()
    caps = provision.detect_caps(sh_for(dev), panel=(720, 1280, 320), renderer="host", gpu_mode=GpuMode.HOST,
                                 hypervisor="WHPX")
    assert caps.api == 36 and caps.abis[0] == "x86_64"
    assert caps.tts_engine == "com.google.android.tts" and caps.tts_ko is True and caps.speech_recognizer
    assert caps.imes and caps.locale == "en-US"


def test_golden_record_round_trip_and_validity(tmp_path: Path) -> None:
    paths = OrcusPaths(tmp_path)
    (paths.avd_data).mkdir(parents=True)
    (paths.avd_data / "config.ini").write_text("hw.ramSize=2560\n", encoding="utf-8")
    paths.state.mkdir(parents=True)
    settings = Settings()
    key = snapshots.current_key(paths, settings)
    rec = snapshots.Record(SessionMode.GOLDEN, "id1", snapshots.golden_key(key), {"locale": "ko-KR"})
    snapshots.write(paths, rec)
    assert snapshots.read(paths) == rec
    assert snapshots.usable(paths, key, SessionMode.GOLDEN) is None          # no snapshot on disk yet
    paths.golden_dir.mkdir(parents=True)
    (paths.golden_dir / "ram.bin").write_bytes(b"x")
    assert snapshots.usable(paths, key, SessionMode.GOLDEN) == rec
    hidden = snapshots.current_key(paths, Settings(engine=EngineSettings(gpu_mode=GpuMode.HOST_HIDDEN)))
    assert snapshots.usable(paths, hidden, SessionMode.GOLDEN) == rec        # host and host_hidden share it
    (paths.avd_data / "config.ini").write_text("hw.ramSize=4096\n", encoding="utf-8")
    assert snapshots.usable(paths, snapshots.current_key(paths, settings), SessionMode.GOLDEN) is None
    assert snapshots.usable(paths, key, SessionMode.COLD) is None
