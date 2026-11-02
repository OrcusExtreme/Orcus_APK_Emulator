"""GPU auto ladder on the fake engine (ARCHITECTURE.md §5.6): host -> host_hidden -> software, a step only
after a software renderer twice in a row (at once in a provisioning boot), remote steps not kept, a retry at host."""

import time
from pathlib import Path

import pytest
from _engine_fakes import GLES_HOST, GLES_SOFT, EngineEnv

from orcus.contracts import EngineState, ErrorCode, GpuMode, SessionMode, Settings
from orcus.contracts.session import EngineSettings
from orcus.engine import gpuladder

S = EngineState
LADDER = Settings(engine=EngineSettings(gpu_mode=GpuMode.LADDER))
HIDDEN = ("-qt-hide-window",)   # the host GPU draws only behind a hidden window


@pytest.fixture
def env(tmp_path: Path):
    e = EngineEnv(tmp_path, settings=LADDER)
    yield e
    e.close()


def wait_for(env: EngineEnv, *states: EngineState, timeout_s: float = 60) -> None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if env.manager.status().state in states:
            return
        time.sleep(0.02)
    raise AssertionError(f"still {env.manager.status().state}, wanted {states}")


def boot(env: EngineEnv):
    env.procs.spawned.clear()
    env.manager.start()
    return env.manager.wait_ready(120)


def stop(env: EngineEnv) -> None:
    env.manager.stop()
    wait_for(env, S.STOPPED)


def flags(env: EngineEnv) -> list[str]:
    return ["hidden" if "-qt-hide-window" in a else "software" if "software" in a else "host"
            for a in env.procs.spawned if "nodaemon" not in a]


def test_the_ladder_stays_at_host_while_it_draws(env: EngineEnv) -> None:
    st = boot(env)
    assert st.caps is not None and st.caps.gpu_mode == GpuMode.HOST
    assert ErrorCode.ENGINE_GPU_LADDER not in st.warnings and st.mode == SessionMode.GOLDEN
    assert gpuladder.level(env.paths, env.ladder_key) == GpuMode.HOST


def test_one_software_boot_is_re_checked_not_stepped_down(env: EngineEnv) -> None:
    boot(env)
    stop(env)
    env.renderers = [GLES_SOFT, GLES_HOST]   # a renderer that came up slow once
    st = boot(env)
    assert env.modes() == ["GOLDEN", "GOLDEN"] and flags(env) == ["host", "host"]
    assert st.caps is not None and st.caps.gpu_mode == GpuMode.HOST
    assert ErrorCode.ENGINE_GPU_LADDER not in st.warnings


def test_software_twice_steps_down_to_host_hidden_and_is_kept(env: EngineEnv) -> None:
    boot(env)
    stop(env)
    env.host_gpu_draws = HIDDEN
    st = boot(env)
    assert flags(env) == ["host", "host", "hidden"]   # the boot, its re-check, then one step down
    assert st.caps is not None and st.caps.gpu_mode == GpuMode.HOST_HIDDEN
    assert ErrorCode.ENGINE_GPU_LADDER in st.warnings and ErrorCode.ENGINE_GPU_SOFTWARE not in st.warnings
    assert gpuladder.level(env.paths, env.ladder_key) == GpuMode.HOST_HIDDEN   # host and host_hidden share
    stop(env)                                                                 # the golden: no rebuild
    boot(env)
    assert flags(env) == ["hidden"]                    # the next start begins at the kept level


def test_the_bottom_rung_is_software_in_cold_mode(env: EngineEnv) -> None:
    boot(env)
    stop(env)
    env.host_gpu_draws = ()                            # the host GPU never draws here
    st = boot(env)
    assert flags(env)[:4] == ["host", "host", "hidden", "hidden"] and "software" in flags(env)
    assert st.mode == SessionMode.COLD and st.caps is not None and st.caps.gpu_mode == GpuMode.SOFTWARE
    assert ErrorCode.ENGINE_GPU_LADDER in st.warnings
    assert gpuladder.level(env.paths, env.ladder_key) == GpuMode.SOFTWARE


def test_a_step_in_a_remote_session_is_not_kept(env: EngineEnv) -> None:
    env.ladder_key = gpuladder.Key("test", "Microsoft Remote Display Adapter", True)
    boot(env)
    stop(env)
    env.host_gpu_draws = HIDDEN
    st = boot(env)
    assert st.caps is not None and st.caps.gpu_mode == GpuMode.HOST_HIDDEN   # this run steps down...
    assert not env.paths.state_file("gpu.json").exists()                    # ...but nothing is kept


def test_retry_gpu_starts_at_host_again(env: EngineEnv) -> None:
    boot(env)
    stop(env)
    env.host_gpu_draws = HIDDEN
    boot(env)
    env.host_gpu_draws = None                          # a new driver: the host GPU draws again
    env.procs.spawned.clear()
    epoch = env.manager.status().epoch
    env.manager.retry_gpu()
    st = env.manager.wait_ready(120, min_epoch=epoch + 1)
    assert flags(env) == ["host"] and st.caps is not None and st.caps.gpu_mode == GpuMode.HOST
    assert ErrorCode.ENGINE_GPU_LADDER not in st.warnings
    assert not env.paths.state_file("gpu.json").exists()


def test_a_provisioning_boot_on_software_steps_down_at_once(tmp_path: Path) -> None:
    env = EngineEnv(tmp_path, settings=LADDER)
    try:
        env.host_gpu_draws = HIDDEN                    # from the very first start
        st = boot(env)
        assert env.modes()[:2] == ["PROVISION", "PROVISION"] and flags(env)[:2] == ["host", "hidden"]
        assert st.mode == SessionMode.GOLDEN and st.caps is not None and st.caps.gpu_mode == GpuMode.HOST_HIDDEN
    finally:
        env.close()
