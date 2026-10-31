"""app.Session over the fake engine world (§3): engine + device + apps wiring, settings, report, shutdown; CLI parts."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from _engine_fakes import EngineEnv
from orcus import cli
from orcus.app import compose, smoke
from orcus.app.session import Session
from orcus.common import messages_ko
from orcus.contracts import EngineState, GpuMode, HeadlessSession, RunEvent, RunStage, SessionApi
from orcus.engine import readonly
from orcus.engine.discovery import DiscoveryRecord


def until(pred: Callable[[], bool], timeout_s: float = 10.0) -> None:
    end = time.monotonic() + timeout_s
    while not pred():
        assert time.monotonic() < end, "condition not met"
        time.sleep(0.01)


@pytest.fixture
def world(tmp_path):   # type: ignore[no-untyped-def]
    env = EngineEnv(tmp_path)
    s = Session(env.paths, env.settings, owner_kind="test", seams=env.seams)
    yield env, s
    s.shutdown(fast=True)
    env.close()


def test_session_wires_engine_device_and_apps(world) -> None:   # type: ignore[no-untyped-def]
    env, s = world
    api: SessionApi = s
    headless: HeadlessSession = s
    assert api.device() is None and headless.attach_readonly() is None   # nobody owns a VM yet
    events: list[RunEvent] = []
    s.apps.subscribe(events.append)
    s.engine.start()
    st = s.engine.wait_ready(60)
    dev = s.device()
    assert dev is not None and dev.epoch == st.epoch and dev.controls is not None
    dev.controls.set_location(37.5, 127.0)   # device-controls thread -> adb test provider (S7)
    until(lambda: any("set-test-provider-location gps --location 37.500000,127.000000" in c
                      for c in env.device.commands))
    report = s.report()
    assert "엔진: ready" in report and "앱: 없음" in report and "build 16428233" in report
    s.open_paths([env.paths.root / "first.apk", env.paths.root / "missing.apk"])
    until(lambda: any(e.stage is RunStage.FAILED for e in events))
    assert {e.run_id for e in events} == {events[0].run_id}   # only the last path was submitted
    s.shutdown()
    assert s.engine.status().state is EngineState.STOPPED


def test_settings_preview_update_and_persist(world) -> None:   # type: ignore[no-untyped-def]
    env, s = world
    r = s.preview_settings({"engine.gpu_mode": "software"})
    assert r.needs_rebuild and r.needs_restart and s.settings().engine.gpu_mode is GpuMode.HOST
    r = s.update_settings({"apk.grant_all": True})
    assert not r.needs_restart and s.settings().apk.grant_all
    saved = json.loads(env.paths.state_file("settings.json").read_text(encoding="utf-8"))
    assert saved == {"apk": {"grant_all": True}}
    with pytest.raises(ValueError):
        s.update_settings({"apk.no_such_key": 1})


def test_readonly_attach_needs_a_live_owner(tmp_path) -> None:   # type: ignore[no-untyped-def]
    env = EngineEnv(tmp_path)
    try:
        assert compose.attach_readonly(env.paths) is None
        env.paths.state_file("engine_owner.json").write_text(json.dumps(
            {"pid": 999_999_999, "kind": "gui", "started": 0, "console": 5640, "grpc": 8640}), encoding="utf-8")
        assert compose.attach_readonly(env.paths) is None   # dead owner
    finally:
        env.close()


def test_cli_setup_checks_the_folder_before_writing_to_it(capsys) -> None:   # type: ignore[no-untyped-def]
    """`orcus-cli setup` used to create <home>\\logs before the folder check, so a bad home crashed (3a review)."""
    assert cli.main(["setup", "--home", "relative\\OrcusAPK"]) == 1
    assert messages_ko.text("path.not_absolute") in capsys.readouterr().out
    assert not (Path("relative") / "OrcusAPK").exists()


def test_readonly_status_is_the_owners_own_state(tmp_path) -> None:   # type: ignore[no-untyped-def]
    """The guest says booted about 4 s before the owner's SESSION_INIT checks end (3a)."""
    env = EngineEnv(tmp_path)
    owner_file = env.paths.state_file("engine_owner.json")

    def write(**state: object) -> None:
        owner_file.write_text(json.dumps({"pid": os.getpid(), "kind": "gui", "started": 0, "console": 5640,
                                          "grpc": env.fake.port, **state}), encoding="utf-8")
    try:
        write(state="session_init", text_key="engine.state.session_init", epoch=1, mode="golden")
        owner = readonly.read_owner(env.paths)
        assert owner is not None and owner.state is EngineState.SESSION_INIT
        rec = DiscoveryRecord(4242, 5640, 5641, env.fake.port, env.fake.token, "orcus36", str(env.paths.avd_data),
                              None, env.paths.tmp / "pid.ini")
        vm = readonly.ReadOnlyVm(owner, rec, env.paths)
        try:
            assert env.fake.booted                           # the guest already says booted...
            st = vm.status()
            assert st is not None and st.state is EngineState.SESSION_INIT and st.text_key.endswith("session_init")
            write(state="ready", text_key="engine.state.ready", epoch=2, mode="golden")
            st = vm.status()                                  # ...and the record is read again on every call
            assert st is not None and (st.state, st.epoch) == (EngineState.READY, 2)
            write(state="no_such_state")                      # unreadable: the guest's word, as before 3a
            st = vm.status()
            assert st is not None and (st.state, st.epoch) == (EngineState.READY, 0)
        finally:
            vm.close()
    finally:
        env.close()


def test_smoke_table_verdict() -> None:
    ok = [smoke.Step("start", "PASS", 4.2, "GOLDEN"), smoke.Step("install", "SKIP", 0.0, "")]
    assert smoke.table(ok).endswith("=> PASS")
    assert smoke.table([*ok, smoke.Step("stop", "FAIL", 30.0, "left behind")]).endswith("=> FAIL")
    xml = ('<node index="0" text="" class="android.widget.ImageView" package="a.b.HelloPurr" content-desc="More" '
           'checkable="false" checked="false" clickable="true" enabled="true" bounds="[656,72][704,136]" />'
           '<node index="0" text="" class="android.widget.Button" package="a.b.HelloPurr" content-desc="" '
           'checkable="false" checked="false" clickable="true" enabled="true" bounds="[0,312][240,552]" />'
           '<node class="android.view.View" package="a.b.HelloPurr" clickable="false" long-clickable="true" '
           'bounds="[0,0][9,9]" />')
    assert smoke.BOUNDS.findall(xml) == [("android.widget.ImageView", "a.b.HelloPurr", "656", "72", "704", "136"),
                                         ("android.widget.Button", "a.b.HelloPurr", "0", "312", "240", "552")]


def test_every_run_stage_has_text() -> None:
    for stage in RunStage:
        assert messages_ko.text(f"run.stage.{stage}")
    for choice in ("replace", "cancel"):
        assert messages_ko.text(f"prompt.choice.{choice}")


def test_cli_parser_knows_the_3a_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    assert "--watch" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["engine", "--help"])
    assert "status" in capsys.readouterr().out
