"""Device test (ARCHITECTURE.md §12.3 items 1, 2, 8): the real EngineManager on the real AVD in D:\\OrcusAPK.

Run: py tools/dev.py test -m device tests/device/test_engine_real.py
The first run after a provisioning change rebuilds the golden (about 3.5 min).
"""

import time

import pytest

from orcus.app import compose
from orcus.common import winjob
from orcus.common.paths import OrcusPaths
from orcus.contracts import EngineState, EpochCause, ErrorCode, SessionMode
from orcus.engine import snapshots
from orcus.engine.manager import EngineManager
from orcus.engine.seams import Seams

pytestmark = pytest.mark.device


@pytest.mark.timeout(900)
def test_real_engine_boot_reset_stop() -> None:
    compose.ensure_job()
    paths = OrcusPaths(compose.make_paths().root)
    settings = compose.load_settings(paths)
    m = EngineManager(paths, lambda: settings, Seams.real(paths), owner_kind="test")
    seen: list[str] = []
    m.subscribe(lambda st: seen.append(str(st.state)))
    try:
        t0 = time.monotonic()
        m.start()
        st = m.wait_ready(720)
        boot_s = time.monotonic() - t0
        print(f"READY after {boot_s:.1f} s, cause {st.epoch_cause}, states {seen}")
        assert st.mode == SessionMode.GOLDEN and st.epoch == 1
        assert ErrorCode.ENGINE_GPU_SOFTWARE not in st.warnings
        assert st.caps is not None and st.caps.api == 36 and st.caps.hypervisor == "WHPX"
        rec = snapshots.read(paths)
        assert rec is not None
        sh = m.shell()
        assert sh.shell(["getprop", "debug.orcus.golden"]).out.strip() == rec.provision_id
        assert sh.shell(["settings", "get", "secure", "stylus_handwriting_enabled"]).out.strip() == "0"

        t1 = time.monotonic()
        target = m.reset_device()
        st = m.wait_ready(120, min_epoch=target)
        print(f"reset in {time.monotonic() - t1:.1f} s")
        assert (st.epoch, st.epoch_cause) == (2, EpochCause.RESET)

        t2 = time.monotonic()
        m.stop()
        end = time.monotonic() + 60
        while m.status().state != EngineState.STOPPED and time.monotonic() < end:
            time.sleep(0.1)
        print(f"stopped in {time.monotonic() - t2:.1f} s")
        assert m.status().state == EngineState.STOPPED
        left = [image for _pid, image in winjob.processes_under(paths.sdk) if not image.lower().endswith("adb.exe")]
        assert left == [], left
    finally:
        m.close()
    assert winjob.processes_under(paths.sdk) == []   # the private adb server goes with close()
