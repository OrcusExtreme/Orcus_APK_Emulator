"""[P4] Keep-state on the fake engine (ARCHITECTURE.md §6.1, §6.3): two slots used in turn, saved at a normal stop
only, a failed save or a kept phone that does not open leaving the old slot alone, a new golden dropping both."""

import time
from pathlib import Path

import pytest
from _engine_fakes import EngineEnv

from orcus.contracts import EngineState, EpochCause, ErrorCode, SessionMode, Settings
from orcus.contracts.session import SessionSettings
from orcus.emugrpc.generated import snapshot_service_pb2 as ss
from orcus.engine import keepstate

S = EngineState
KEEP = Settings(session=SessionSettings(keep_state=True))
MINE = "debug.app.data"   # the student's app data in the phone, as far as the fake phone can tell


def wait_for(env: EngineEnv, *states: EngineState, timeout_s: float = 30) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if env.manager.status().state in states:
            return
        time.sleep(0.02)
    raise AssertionError(f"still {env.manager.status().state}, wanted {states}")


@pytest.fixture
def env(tmp_path: Path):
    e = EngineEnv(tmp_path, settings=KEEP)
    yield e
    e.close()


def session(env: EngineEnv, *, data: str | None = None):   # type: ignore[no-untyped-def]
    """Start, wait for READY, optionally leave some app data in the phone."""
    env.procs.spawned.clear()
    env.manager.start()
    st = env.manager.wait_ready(30)
    if data is not None:
        env.device.props[MINE] = data
    return st


def stop(env: EngineEnv, *, fast: bool = False) -> None:
    env.manager.stop(fast=fast)
    wait_for(env, S.STOPPED)


def saves(env: EngineEnv) -> list[str]:
    return [arg for name, arg in env.fake.calls if name == "SaveSnapshot" and str(arg).startswith("orcus_user_")]


def test_a_normal_stop_keeps_the_phone_and_the_next_start_opens_it(env: EngineEnv) -> None:
    st = session(env, data="mine")
    assert st.mode == SessionMode.KEEP_STATE   # the chip: "휴대폰 유지 모드"
    stop(env)
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "a" and not p.once and keepstate.exists(env.paths, "a")
    env.device.props[MINE] = "lost"            # what the phone would hold without the kept one
    st = session(env)
    assert env.modes() == ["KEEP_a"] and env.device.props[MINE] == "mine"
    assert (st.mode, st.epoch, st.epoch_cause) == (SessionMode.KEEP_STATE, 2, EpochCause.BOOT)
    env.device.props[MINE] = "more"
    stop(env)                                  # into the other slot, then the old one goes
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "b"
    assert keepstate.exists(env.paths, "b") and not keepstate.exists(env.paths, "a")
    session(env)
    assert env.modes() == ["KEEP_b"] and env.device.props[MINE] == "more"


def test_fast_stops_and_restarts_never_save(env: EngineEnv) -> None:
    session(env, data="mine")
    stop(env, fast=True)                       # logoff, low disk (§6.3)
    assert keepstate.read(env.paths) is None and saves(env) == []
    session(env)
    assert env.procs.emulator is not None
    env.procs.emulator.kill()                  # a crash restart does not save either
    env.manager.wait_ready(30, min_epoch=3)
    assert saves(env) == []


def test_a_crash_restart_opens_the_kept_phone_again(env: EngineEnv) -> None:
    session(env, data="mine")
    stop(env)
    session(env)
    env.device.props[MINE] = "lost"
    assert env.procs.emulator is not None
    env.procs.emulator.kill()
    st = env.manager.wait_ready(30, min_epoch=3)
    assert st.epoch_cause == EpochCause.CRASH_RESTART
    assert env.modes() == ["KEEP_a", "KEEP_a"] and env.device.props[MINE] == "mine"


def test_a_failed_save_leaves_the_old_slot_and_says_so_next_time(env: EngineEnv) -> None:
    session(env, data="first")
    stop(env)
    session(env, data="second")
    env.fake.script_snapshot("SaveSnapshot", ss.SnapshotPackage(snapshot_id="orcus_user_b", success=False,
                                                                err=b"disk full"))
    stop(env)
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "a" and "disk full" in p.save_failed and keepstate.exists(env.paths, "a")
    st = session(env)
    assert env.modes() == ["KEEP_a"] and env.device.props[MINE] == "first"
    assert ErrorCode.ENGINE_KEEP_SAVE_FAILED in st.warnings
    p = keepstate.read(env.paths)
    assert p is not None and p.save_failed == ""   # said once


@pytest.mark.parametrize("how", ["cold boot", "exit", "hang before gRPC"])
def test_a_kept_phone_that_does_not_open_starts_clean_and_is_left_alone(env: EngineEnv, how: str) -> None:
    """§6.3: no forced load; a slot that does not open (no golden marker: a cold boot; the emulator exits; or it
    hangs before gRPC answers, 4a device test) starts from the golden, keeps the slot, and that session is not kept
    over it."""
    session(env, data="mine")
    stop(env)
    env.broken_slot = how == "cold boot"
    env.slot_load_exits = how == "exit"
    env.slot_hangs = how == "hang before gRPC"
    st = session(env)
    assert env.modes() == ["KEEP_a", "GOLDEN"]
    assert ErrorCode.ENGINE_KEEP_FAILED in st.warnings and st.mode == SessionMode.KEEP_STATE
    assert keepstate.exists(env.paths, "a")
    before = len(saves(env))
    stop(env)
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "a" and len(saves(env)) == before   # not overwritten
    env.broken_slot = env.slot_load_exits = env.slot_hangs = False
    session(env)
    assert env.modes() == ["KEEP_a"] and env.device.props[MINE] == "mine"   # a later start opens it again


def test_a_new_golden_drops_the_kept_phone(env: EngineEnv) -> None:
    session(env, data="mine")
    stop(env)
    env.manager.rebuild("GPU 모드를 바꿈")
    st = env.manager.wait_ready(120)
    assert keepstate.read(env.paths) is None and not keepstate.exists(env.paths, "a")
    assert ErrorCode.ENGINE_KEEP_GONE in st.warnings


def test_keep_this_time_opens_the_phone_once(tmp_path: Path) -> None:
    """The close notice's [이번에는 휴대폰 유지하고 닫기] with keeping off (§6.1)."""
    env = EngineEnv(tmp_path)
    try:
        st = session(env, data="mine")
        assert st.mode == SessionMode.GOLDEN
        stop(env)
        assert keepstate.read(env.paths) is None   # an ordinary close forgets
        session(env, data="mine")
        env.manager.keep_once()
        stop(env)
        p = keepstate.read(env.paths)
        assert p is not None and p.once and keepstate.exists(env.paths, "a")
        st = session(env)
        assert env.modes() == ["KEEP_a"] and env.device.props[MINE] == "mine" and st.mode == SessionMode.GOLDEN
        stop(env)                                  # not kept again: the next start is the clean golden
        assert keepstate.read(env.paths) is None and not keepstate.exists(env.paths, "a")
        session(env)
        assert env.modes() == ["GOLDEN"]
    finally:
        env.close()


def test_turning_keeping_off_frees_the_slots(env: EngineEnv) -> None:
    session(env, data="mine")
    stop(env)
    env.settings = Settings()
    st = session(env)
    assert env.modes() == ["GOLDEN"] and st.mode == SessionMode.GOLDEN
    assert keepstate.read(env.paths) is None and not keepstate.exists(env.paths, "a")


def test_no_display_never_opens_or_overwrites_the_kept_phone(env: EngineEnv) -> None:
    session(env, data="mine")
    stop(env)
    env.displays = 0                           # a disconnected Remote Desktop session (3a finding 18)
    st = session(env)
    assert env.modes() == ["COLD"] and ErrorCode.ENGINE_NO_DISPLAY in st.warnings
    before = len(saves(env))
    stop(env)
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "a" and len(saves(env)) == before


def test_reset_in_keep_mode_loads_the_golden_and_the_stop_keeps_that(env: EngineEnv) -> None:
    first = session(env, data="mine")
    target = env.manager.reset_device()
    st = env.manager.wait_ready(30, min_epoch=target)
    assert st.epoch == first.epoch + 1 and MINE not in env.device.props
    stop(env)
    session(env)
    assert env.modes() == ["KEEP_a"] and MINE not in env.device.props


def test_not_enough_disk_is_a_failed_save(env: EngineEnv) -> None:
    session(env, data="mine")
    env.free = 2 * 1024 ** 3                   # under the 3 GiB a save needs (still enough to run)
    stop(env)
    p = keepstate.read(env.paths)
    assert p is not None and p.slot == "" and "GiB free" in p.save_failed and saves(env) == []
