"""EngineManager state machine (ARCHITECTURE.md §5.1) on fakes: provisioning, READY, reset, stop, crashes."""

import time
from concurrent.futures import CancelledError
from pathlib import Path

import pytest
from _engine_fakes import GLES_SOFT, EngineEnv

from orcus.common import winjob
from orcus.common.paths import GOLDEN_SNAPSHOT
from orcus.contracts import (EngineControl, EngineNotReady, EngineState, EpochCause, ErrorCode, GpuMode, OrcusFailure,
                             SessionMode, Settings)
from orcus.contracts.session import EngineSettings
from orcus.emugrpc.generated import snapshot_service_pb2 as ss
from orcus.engine import snapshots
from orcus.engine.manager import EngineManager

S = EngineState


@pytest.fixture
def env(tmp_path: Path):
    e = EngineEnv(tmp_path)
    yield e
    e.close()


def wait_for(env: EngineEnv, *states: EngineState, timeout_s: float = 30) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if env.manager.status().state in states:
            return
        time.sleep(0.02)
    st = env.manager.status()
    raise AssertionError(f"still {st.state} ({st.error}) after {timeout_s} s, wanted {states}")


def ready(env: EngineEnv, **kw: object):   # type: ignore[no-untyped-def]
    return env.manager.wait_ready(30, **kw)   # type: ignore[arg-type]


def test_manager_satisfies_engine_control(env: EngineEnv) -> None:
    _: EngineControl = env.manager
    assert env.manager.status().state == S.STOPPED


def test_first_start_provisions_verifies_and_is_ready(env: EngineEnv) -> None:
    env.manager.start()
    st = ready(env)
    assert (st.epoch, st.epoch_cause, st.mode) == (1, EpochCause.BOOT, SessionMode.GOLDEN)
    # provisioning boot, clean cold boot that saves, verification boot from the golden
    assert env.modes() == ["PROVISION", "COLD", "GOLDEN"]
    rec = snapshots.read(env.paths)
    assert rec is not None and rec.mode == SessionMode.GOLDEN
    assert env.device.golden_props is not None
    assert env.device.golden_props["debug.orcus.golden"] == rec.provision_id
    assert env.device.golden_props["debug.orcus.session"] == ""
    assert env.device.settings[("secure", "stylus_handwriting_enabled")] == "0"
    assert ("SaveSnapshot", GOLDEN_SNAPSHOT) in env.fake.calls
    assert st.caps is not None and st.caps.api == 36 and st.caps.tts_engine == "com.google.android.tts"
    assert env.sessions[-1].epoch == 1 and not env.sessions[-1].closed


def test_second_run_boots_the_golden_only(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.manager.stop()
    wait_for(env, S.STOPPED)
    env.procs.spawned.clear()
    env.manager.start()
    st = ready(env)
    assert env.modes() == ["GOLDEN"]
    assert (st.epoch, st.epoch_cause) == (2, EpochCause.BOOT)


def test_reset_raises_epoch_by_exactly_one(env: EngineEnv) -> None:
    env.manager.start()
    first = ready(env)
    old_session = env.sessions[-1]
    target = env.manager.reset_device()
    assert target == first.epoch + 1
    st = ready(env, min_epoch=target)
    assert (st.epoch, st.epoch_cause) == (first.epoch + 1, EpochCause.RESET)
    assert ("LoadSnapshot", GOLDEN_SNAPSHOT) in env.fake.calls
    assert old_session.closed and old_session.input.released == 1
    assert env.sessions[-1].epoch == target


def test_shell_is_bound_to_its_epoch(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    shell = env.manager.shell()
    assert shell.shell(["echo", "ok"]).out == "ok"
    env.manager.reset_device()
    ready(env, min_epoch=2)
    with pytest.raises(EngineNotReady):
        shell.shell(["echo", "ok"])
    assert env.manager.shell().shell(["echo", "ok"]).out == "ok"


def test_shell_quotes_each_argument(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.manager.shell().shell(["setprop", "debug.test", ""])
    assert env.device.commands[-1] == "setprop debug.test ''"


def test_stop_shuts_down_and_releases_the_lock(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.manager.stop()
    wait_for(env, S.STOPPED)
    assert any(c == ("setVmState", 3) or c[0] == "setVmState" for c in env.fake.calls)
    other = winjob.ExclusiveLock(env.paths.state_file("engine.lock"))
    other.acquire()   # free again
    other.release()
    assert not env.paths.state_file("engine_owner.json").exists()
    assert env.sessions[-1].closed


def test_lock_held_elsewhere_fails_without_touching_anything(env: EngineEnv) -> None:
    other = winjob.ExclusiveLock(env.paths.state_file("engine.lock"))
    other.acquire()
    try:
        env.manager.start()
        wait_for(env, S.FAILED)
        st = env.manager.status()
        assert st.error is not None and st.error.code == ErrorCode.ENGINE_IN_USE
        assert env.procs.spawned == []
        with pytest.raises(OrcusFailure):
            ready(env)
    finally:
        other.release()


def test_not_installed(tmp_path: Path) -> None:
    e = EngineEnv(tmp_path)
    try:
        Path(f"{e.paths.adbkey}.pub").unlink()
        m = EngineManager(e.paths, lambda: e.settings, e.seams)
        assert m.status().state == S.NOT_INSTALLED
        m.start()
        with pytest.raises(OrcusFailure) as info:
            m.wait_ready(10)
        assert info.value.error.code == ErrorCode.PRE_NOT_INSTALLED
        m.close()
    finally:
        e.close()


def test_crash_restarts_once_then_fails(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    assert env.procs.emulator is not None
    env.procs.emulator.kill()          # emulator dies on its own
    wait_for(env, S.STOPPING, S.LAUNCHING, S.CONNECTING, S.BOOTING, S.SESSION_INIT)
    st = ready(env, min_epoch=2)
    assert st.epoch_cause == EpochCause.CRASH_RESTART
    env.procs.emulator.kill()          # second crash within five minutes
    wait_for(env, S.FAILED)
    st = env.manager.status()
    assert st.error is not None and st.error.code == ErrorCode.ENGINE_CRASHED


def test_wrong_golden_marker_rebuilds_once_then_fails(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.manager.stop()
    wait_for(env, S.STOPPED)
    assert env.device.golden_props is not None
    env.device.golden_props["debug.orcus.golden"] = "someone-else"   # the golden is not ours any more
    env.procs.spawned.clear()
    env.manager.start()
    st = ready(env, timeout_s=60) if False else env.manager.wait_ready(60)
    assert st.epoch_cause == EpochCause.REBUILD
    assert env.modes() == ["GOLDEN", "REBUILD", "COLD", "GOLDEN"]
    # break it again in the same run: the failure-rebuild budget (1) is spent
    env.manager.stop()
    wait_for(env, S.STOPPED)
    env.device.golden_props["debug.orcus.golden"] = "someone-else"
    env.manager.start()
    wait_for(env, S.FAILED, timeout_s=60)
    st = env.manager.status()
    assert st.error is not None and st.error.code == ErrorCode.ENGINE_GOLDEN_LOAD_FAILED


def test_missing_golden_rebuilds(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.manager.stop()
    wait_for(env, S.STOPPED)
    env.fake.snapshots.discard(GOLDEN_SNAPSHOT)   # snapshot gone, provision.json still there
    env.procs.spawned.clear()
    env.manager.start()
    st = env.manager.wait_ready(60)
    assert st.epoch_cause == EpochCause.REBUILD
    assert env.modes()[0] == "REBUILD"


def test_reset_that_cannot_be_verified_restarts(env: EngineEnv) -> None:
    env.manager.start()
    ready(env)
    env.fake.script_snapshot("LoadSnapshot", ss.SnapshotPackage(snapshot_id=GOLDEN_SNAPSHOT, success=False,
                                                                err=b"boom"))
    target = env.manager.reset_device()
    st = ready(env, min_epoch=target)
    assert st.epoch_cause == EpochCause.RESET_RECOVERY
    assert ErrorCode.ENGINE_RESET_FAILED in st.warnings


def test_provisioning_failure_never_saves(env: EngineEnv) -> None:
    env.device.never_persist.add(("secure", "stylus_handwriting_enabled"))
    env.manager.start()
    wait_for(env, S.FAILED, timeout_s=60)
    st = env.manager.status()
    assert st.error is not None and st.error.code == ErrorCode.ENGINE_PROVISION_FAILED
    assert "settings" in st.error.detail
    assert not any(c[0] == "SaveSnapshot" for c in env.fake.calls)
    assert snapshots.read(env.paths) is None


def test_settings_lost_without_flush_are_caught_before_saving(env: EngineEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    from orcus.engine import provision
    monkeypatch.setattr(provision, "flush", lambda sh, clock: True)   # forget the sync: the last write is lost
    env.manager.start()
    wait_for(env, S.FAILED, timeout_s=60)
    st = env.manager.status()
    assert st.error is not None and "not kept after the reboot" in st.error.detail
    assert not any(c[0] == "SaveSnapshot" for c in env.fake.calls)


def test_software_gpu_runs_cold_with_a_banner(tmp_path: Path) -> None:
    e = EngineEnv(tmp_path, settings=Settings(engine=EngineSettings(gpu_mode=GpuMode.SOFTWARE)))
    try:
        e.device.renderer = GLES_SOFT
        e.manager.start()
        st = e.manager.wait_ready(60)
        assert st.mode == SessionMode.COLD
        assert ErrorCode.ENGINE_GPU_SOFTWARE in st.warnings
        assert "software" in next(a for a in e.procs.spawned if "nodaemon" not in a)
        assert not any(c[0] == "SaveSnapshot" for c in e.fake.calls)
        e.procs.spawned.clear()
        target = e.manager.reset_device()   # COLD: reset is a rebuild
        st = e.manager.wait_ready(60, min_epoch=target)
        assert st.epoch_cause == EpochCause.REBUILD and e.modes()[0] == "REBUILD"
    finally:
        e.close()


def test_wait_ready_can_be_cancelled(env: EngineEnv) -> None:
    import threading
    cancel = threading.Event()
    cancel.set()
    env.manager.start()
    with pytest.raises(CancelledError):
        env.manager.wait_ready(5, min_epoch=99, cancel=cancel)


def test_wait_ready_fails_when_stopped(env: EngineEnv) -> None:
    with pytest.raises(OrcusFailure):
        env.manager.wait_ready(5)
