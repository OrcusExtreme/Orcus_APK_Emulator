"""Acceptance on the real phone (plan.md §3): keep-state (§6.3) and the GPU ladder (§5.6).

Run: py tools/dev.py test -m device tests/device/test_acceptance_4a.py -s
The tests turn keeping on in memory only (settings.json is not touched) and drop the kept phones they made at the
end, so the next ordinary start is the golden again. Lines starting with RESULT are for plan.md §4.
- round trip: a normal stop keeps the phone (RAM and disk), the next start opens it;
- a stop killed while saving leaves the previous kept phone, said at the next start;
- a kept phone that does not open (its ram.bin cut short, no forced load) starts clean and is left alone;
- a phone kept turned comes back turned, and the controls know it;
- with the GPU ladder, a PC whose GPU draws starts on the golden at host.
"""

import dataclasses
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from orcus.app import compose
from orcus.app.session import Session
from orcus.common import win32, winjob
from orcus.common.paths import OrcusPaths
from orcus.apk import launch
from orcus.contracts import EngineState, ErrorCode, PhoneWorld, Rotation, SessionMode
from orcus.contracts.session import SessionSettings
from orcus.engine import keepstate
from test_acceptance_3a import result, until

PROBE_FILE = "/data/local/tmp/orcus_keep_probe"
PROBE_PROP = "debug.orcus.keep_probe"

pytestmark = [pytest.mark.device]


class Kept:
    def __init__(self) -> None:
        compose.ensure_job()
        self.paths: OrcusPaths = compose.make_paths()
        print(f"\nlog: {compose.start_logging(self.paths, 'acceptance4a')}")
        settings = compose.load_settings(self.paths)
        keepstate.drop(self.paths)   # start from the golden whatever an earlier run left
        self.s = Session(self.paths, dataclasses.replace(settings, session=SessionSettings(keep_state=True)),
                         owner_kind="test")

    def start(self, timeout_s: float = 900):
        t = time.monotonic()
        self.s.engine.start()
        st = self.s.engine.wait_ready(timeout_s)
        return st, time.monotonic() - t

    def stop(self) -> float:
        t = time.monotonic()
        self.s.engine.stop()
        until(lambda: self.s.engine.status().state == EngineState.STOPPED, 120, "STOPPED")
        return time.monotonic() - t

    def mark(self, token: str) -> None:
        sh = self.s.engine.shell()
        sh.shell(["sh", "-c", f"echo {token} > {PROBE_FILE}"], timeout_s=15)
        sh.shell(["setprop", PROBE_PROP, token], timeout_s=15)

    def marks(self) -> tuple[str, str]:
        sh = self.s.engine.shell()
        return (sh.shell(["cat", PROBE_FILE], timeout_s=15).out.strip(),
                sh.shell(["getprop", PROBE_PROP], timeout_s=15).out.strip())

    def slot_mb(self, slot: str) -> float:
        d = keepstate.slot_dir(self.paths, slot)
        return round(sum(f.stat().st_size for f in d.iterdir() if f.is_file()) / 1e6, 1) if d.exists() else 0.0


@pytest.fixture(scope="module")
def kept() -> Iterator[Kept]:
    k = Kept()
    yield k
    k.s.shutdown()
    keepstate.drop(k.paths)   # the phones these tests kept: the next ordinary start is the golden


@pytest.mark.timeout(1200)
def test_a_normal_stop_keeps_the_phone_and_the_next_start_opens_it(kept: Kept) -> None:
    st, _ = kept.start()
    assert st.mode == SessionMode.KEEP_STATE
    token = uuid.uuid4().hex[:8]
    kept.mark(token)
    stop_s = kept.stop()
    p = keepstate.read(kept.paths)
    assert p is not None and p.slot == "a" and keepstate.exists(kept.paths, "a")
    st, boot_s = kept.start()
    assert st.mode == SessionMode.KEEP_STATE and kept.marks() == (token, token)   # disk and RAM came back
    result("keep-state round trip", stop_with_save_s=round(stop_s, 1), open_s=round(boot_s, 1),
           slot_mb=kept.slot_mb("a"))
    token2 = uuid.uuid4().hex[:8]
    kept.mark(token2)
    kept.stop()                                  # into slot b, then a goes
    p = keepstate.read(kept.paths)
    assert p is not None and p.slot == "b" and not keepstate.exists(kept.paths, "a")
    kept.start()
    assert kept.marks() == (token2, token2)


def qemu_pids(paths: OrcusPaths) -> list[int]:
    return [pid for pid, image in winjob.processes_under(paths.sdk) if "qemu-system" in Path(image).name.lower()]


@pytest.mark.timeout(1200)
def test_a_stop_killed_while_saving_leaves_the_previous_kept_phone(kept: Kept) -> None:
    before = kept.marks()                        # the phone kept in b by the test above, opened again
    kept.mark("unsaved")
    kept.s.engine.stop()
    until(lambda: kept.s.engine.status().text_key == "engine.state.keeping", 30, "saving")
    time.sleep(0.4)
    pids = qemu_pids(kept.paths)
    for pid in pids:
        subprocess.run([str(win32.system_dir() / "taskkill.exe"), "/F", "/PID", str(pid)], capture_output=True)
    until(lambda: kept.s.engine.status().state == EngineState.STOPPED, 120, "STOPPED")
    p = keepstate.read(kept.paths)
    assert p is not None and p.slot == "b" and p.save_failed and keepstate.exists(kept.paths, "b")
    st, _ = kept.start()
    assert kept.marks() == before and ErrorCode.ENGINE_KEEP_SAVE_FAILED in st.warnings
    result("kill while saving", killed=pids, failure=p.save_failed[:80], reopened="previous kept phone")


@pytest.mark.timeout(1200)
def test_a_kept_phone_that_does_not_open_starts_clean_and_stays(kept: Kept) -> None:
    kept.stop()                                  # keeps the current phone (b -> a)
    p = keepstate.read(kept.paths)
    assert p is not None and p.slot == "a"
    ram = keepstate.slot_dir(kept.paths, "a") / "ram.bin"
    with ram.open("r+b") as f:                   # cut short: S4 saw a forced load of such a snapshot hang
        f.truncate(ram.stat().st_size // 2)
    st, boot_s = kept.start()
    assert ErrorCode.ENGINE_KEEP_FAILED in st.warnings and st.mode == SessionMode.KEEP_STATE
    assert kept.marks()[0] in ("", "unsaved") or "No such file" in kept.marks()[0]
    slot_left = keepstate.slot_dir(kept.paths, "a").exists()
    result("broken kept phone", started_clean_after_s=round(boot_s, 1), slot_dir_left=slot_left,
           ram_left=ram.exists())
    kept.stop()                                  # that clean session is not kept over the slot
    p = keepstate.read(kept.paths)
    assert p is not None and p.slot == "a"


@pytest.mark.timeout(1200)
def test_a_kept_phone_comes_back_turned_and_the_controls_know_it(kept: Kept) -> None:
    """A phone kept in landscape, lit and low comes back that way; the controls read it at the start,
    so the next turn starts there and the [Sensors] tab shows its light and battery."""
    keepstate.drop(kept.paths)                   # the broken slot of the test above
    kept.start()
    dev = kept.s.device()
    assert dev is not None and dev.controls is not None
    dev.controls.rotate(Rotation.LANDSCAPE)
    dev.controls.set_light(123.0)
    dev.controls.set_battery(30, False)
    time.sleep(3.0)
    kept.stop()
    kept.start()
    dev = kept.s.device()
    assert dev is not None and dev.controls is not None
    assert dev.controls.posture == Rotation.LANDSCAPE
    assert dev.controls.world == PhoneWorld(123.0, 1.0, 30, False)
    dev.controls.rotate(Rotation.PORTRAIT)
    shell = kept.s.engine.shell()
    until(lambda: launch.display_rotation(shell) == Rotation.PORTRAIT, 15, "display back to portrait")
    result("kept phone turned", posture="LANDSCAPE", world=dev.controls.world)
    kept.stop()


@pytest.mark.timeout(900)
def test_the_gpu_ladder_starts_on_the_golden_at_host() -> None:
    """§5.6: switching to the ladder keeps the golden (host and host_hidden share it); here the GPU draws, so
    it stays at host and keeps no level."""
    from orcus.contracts import GpuMode
    compose.ensure_job()
    paths = compose.make_paths()
    print(f"\nlog: {compose.start_logging(paths, 'acceptance4a')}")
    settings = compose.load_settings(paths)
    ladder = dataclasses.replace(settings, engine=dataclasses.replace(settings.engine, gpu_mode=GpuMode.LADDER))
    s = Session(paths, ladder, owner_kind="test")
    try:
        t = time.monotonic()
        s.engine.start()
        st = s.engine.wait_ready(900)
        assert st.caps is not None and st.caps.gpu_mode == GpuMode.HOST and st.epoch_cause.name == "BOOT"
        assert ErrorCode.ENGINE_GPU_LADDER not in st.warnings
        assert not paths.state_file("gpu.json").exists()
        result("ladder on this PC", ready_s=round(time.monotonic() - t, 1), renderer=st.caps.renderer[-40:])
    finally:
        s.shutdown()
