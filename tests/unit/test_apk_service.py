"""ApkService on a fake engine and phone (§6.2, §7, §8, §12.1): stages, fast path, epochs, newest drop, prompts,
pre-rotation, the watcher feeding run events, and the app commands."""

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from _apk_fakes import SYSTEM_PID, Events, FakeEngine, fake_inspect, make_apk, now_us
from conftest import REPO
from orcus.apk import launch
from orcus.apk import service as service_mod
from orcus.apk.service import Service
from orcus.common import messages_ko
from orcus.contracts import (
    ApkService,
    ApkSettings,
    EngineControl,
    EngineState,
    EpochCause,
    ErrorCode,
    IssueCode,
    LogEntry,
    RemedyAction,
    Rotation,
    RunEvent,
    RunPolicy,
    RunStage,
    SessionMode,
)

PKG = "appinventor.ai_test.HelloPurr"
S = RunStage


@pytest.fixture(autouse=True)
def _manifests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_mod, "inspect", fake_inspect)


class World:
    def __init__(self, tmp_path: Path, engine: FakeEngine | None = None, settings: ApkSettings | None = None,
                 mic_for: tuple[str, ...] = (), orient_check_s: float = 60.0, orient_soon_s: float = 60.0) -> None:
        self.engine = engine or FakeEngine()
        self.phone = self.engine.phone
        self.events = Events()
        self.dir = tmp_path
        self.staging = tmp_path / "staging"
        chosen = settings or ApkSettings()
        self.mic_for = list(mic_for)
        self.svc = Service(cast(EngineControl, self.engine), self.staging, lambda: chosen,
                           mic_allowed=lambda pkg: pkg in self.mic_for, sleep=lambda s: None,
                           orient_check_s=orient_check_s, orient_soon_s=orient_soon_s)
        self.svc.subscribe(self.events)

    def apk(self, name: str = "HelloPurr", package: str = PKG, **manifest: object) -> Path:
        return make_apk(self.dir, name, package, **manifest)

    def stage_is(self, run_id: str, stage: RunStage, *, after: int = 0, epoch: int | None = None) -> RunEvent:
        return self.events.wait(lambda e: e.run_id == run_id and e.stage is stage
                                and (epoch is None or e.epoch == epoch), after=after)


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Callable[..., World]]:
    made: list[World] = []

    def factory(**kw: object) -> World:
        w = World(tmp_path, **kw)  # type: ignore[arg-type]
        made.append(w)
        return w
    yield factory
    for w in made:
        w.svc.close()


def until(pred: Callable[[], bool], timeout_s: float = 5.0) -> None:
    end = time.monotonic() + timeout_s
    while not pred():
        assert time.monotonic() < end, "condition not met"
        time.sleep(0.01)


def test_service_satisfies_the_contract(make: Callable[..., World]) -> None:
    _: ApkService = make().svc


def test_drop_goes_through_every_stage(make: Callable[..., World]) -> None:
    w = make()
    rid = w.svc.submit(w.apk())
    ev = w.stage_is(rid, S.RUNNING)
    assert w.events.stages(rid) == ["STAGING", "INSPECTING", "CHECKING", "WAITING_ENGINE", "INSTALLING",
                                    "LAUNCHING", "RUNNING"]
    assert ev.pid == w.phone.running[PKG] and ev.epoch == 1 and ev.info is not None and ev.info.package == PKG
    assert w.phone.installs == [(PKG, ["-r"])]
    assert ("am", "start", "-W", "-n", f"{PKG}/{PKG}.Screen1") in w.phone.commands


def test_same_file_in_the_same_epoch_skips_only_the_install(make: Callable[..., World]) -> None:
    w = make()
    apk = w.apk()
    w.stage_is(w.svc.submit(apk), S.RUNNING)
    rid = w.svc.submit(apk)
    ev = w.stage_is(rid, S.RUNNING)
    assert len(w.phone.installs) == 1 and "INSTALLING" not in w.events.stages(rid)
    assert "CHECKING" in w.events.stages(rid) and "WAITING_ENGINE" in w.events.stages(rid)
    starts = [i for i, c in enumerate(w.phone.commands) if c[:2] == ("am", "start")]
    stop = w.phone.commands.index(("am", "force-stop", PKG))
    assert starts[0] < stop < starts[1] and ev.pid == w.phone.running[PKG]
    w.phone.installs.clear()
    w.stage_is(w.svc.submit(apk, RunPolicy.FRESH_APP), S.RUNNING, after=w.events.mark())
    assert len(w.phone.installs) == 1 and w.phone.uninstalls == [PKG]   # FRESH_APP never takes the fast path


def test_reset_leaves_the_phone_clean_and_the_next_drop_installs_again(make: Callable[..., World]) -> None:
    w = make()
    apk = w.apk()
    rid = w.svc.submit(apk)
    w.stage_is(rid, S.RUNNING)
    w.engine.new_epoch(EpochCause.RESET)
    w.stage_is(rid, S.EXITED)
    assert len(w.phone.installs) == 1
    ev = w.stage_is(w.svc.submit(apk), S.RUNNING, after=w.events.mark())
    assert ev.epoch == 2 and len(w.phone.installs) == 2


def test_crash_restart_reinstalls_the_running_app(make: Callable[..., World]) -> None:
    w = make()
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.RUNNING)
    w.engine.new_epoch(EpochCause.CRASH_RESTART)
    ev = w.stage_is(rid, S.RUNNING, epoch=2)
    assert len(w.phone.installs) == 2 and w.phone.running[PKG] == ev.pid


def test_fresh_phone_waits_for_the_new_epoch_and_cold_uses_fresh_app(make: Callable[..., World]) -> None:
    w = make(settings=ApkSettings(reset_before_each_run=True))
    ev = w.stage_is(w.svc.submit(w.apk()), S.RUNNING)
    assert w.engine.resets == 1 and ev.epoch == 2
    cold = make(engine=FakeEngine(mode=SessionMode.COLD))
    cold.phone.packages[PKG] = 1
    ev = cold.stage_is(cold.svc.submit(cold.apk(), RunPolicy.FRESH_PHONE), S.RUNNING)
    assert cold.engine.resets == 0 and cold.phone.uninstalls == [PKG]
    assert IssueCode.COLD_FRESH_APP in [i.code for i in ev.issues]   # said on the app card (3b)


def test_microphone_follows_the_consent_of_the_app_being_launched(make: Callable[..., World]) -> None:
    """§9.8: on for an app the user allowed, re-applied after a crash restart, off again for the next app."""
    w = make(mic_for=(PKG,))
    rid = w.svc.submit(w.apk(permissions=["android.permission.RECORD_AUDIO"]))
    w.stage_is(rid, S.RUNNING)
    assert w.engine.current().controls.mics == [True]
    w.engine.new_epoch(EpochCause.CRASH_RESTART)           # SESSION_INIT turned it off: the new controls say so
    w.stage_is(rid, S.RUNNING, epoch=2)
    assert w.engine.current().controls.mics == [True]
    w.stage_is(w.svc.submit(w.apk("B", "appinventor.ai_test.B")), S.RUNNING)
    assert w.engine.current().controls.mics == [True, False]


def test_location_apps_get_the_location_again_when_a_screen_starts_or_resumes(make: Callable[..., World]) -> None:
    """Test providers report only when told: the app that starts listening gets the phone's location again (3b)."""
    w = make()
    rid = w.svc.submit(w.apk(permissions=["android.permission.ACCESS_FINE_LOCATION"]))
    running = w.stage_is(rid, S.RUNNING)
    controls = w.engine.current().controls
    until(lambda: controls.resent == 1)                     # at the launch
    assert running.pid is not None
    w.engine.current().push(LogEntry(now_us(), running.pid, running.pid, "I", "Form", "Form Screen1 got onResume"))
    until(lambda: controls.resent == 2)                     # back from the permission dialog, say
    w.engine.current().push(LogEntry(now_us(), SYSTEM_PID, SYSTEM_PID, "I", "Form", "Form Screen1 got onResume"))
    time.sleep(0.2)
    assert controls.resent == 2                             # another process's line is not our app
    plain = make()
    plain.stage_is(plain.svc.submit(plain.apk()), S.RUNNING)
    time.sleep(0.2)
    assert plain.engine.current().controls.resent == 0      # no location permission: nothing sent


def test_a_turned_app_screen_raises_the_banner_and_clears_it(make: Callable[..., World]) -> None:
    """A landscape Screen2 in a portrait posture: banner after two checks in a row, gone once they match."""
    w = make(orient_check_s=0.05, orient_soon_s=0.05)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.RUNNING)
    mark = w.events.mark()
    w.phone.display = "ROTATION_90"
    ev = w.events.wait(lambda e: e.run_id == rid and e.warnings == (ErrorCode.APP_ORIENTATION_MISMATCH,),
                       after=mark)
    assert ev.stage is S.RUNNING
    assert messages_ko.spec(ErrorCode.APP_ORIENTATION_MISMATCH).remedy is RemedyAction.MATCH_ORIENTATION
    mark = w.events.mark()
    w.svc.match_orientation()                                # turns the phone to the display
    w.events.wait(lambda e: e.run_id == rid and e.warnings == (), after=mark)
    assert w.engine.current().controls.rotations == [Rotation.LANDSCAPE]
    w.phone.display = "ROTATION_0"                           # Screen2 closed: portrait app in a landscape posture
    mark = w.events.mark()
    w.events.wait(lambda e: e.warnings == (ErrorCode.APP_ORIENTATION_MISMATCH,), after=mark)
    mark = w.events.mark()
    w.svc.stop_app()
    assert w.stage_is(rid, S.EXITED, after=mark).warnings == ()   # an exited app has no banner


def test_the_orientation_check_keeps_its_pace_while_commands_keep_coming(make: Callable[..., World]) -> None:
    """By the clock: a command every 20 ms restarted the 50 ms wait, so the check never ran (3b review)."""
    w = make(orient_check_s=0.05, orient_soon_s=0.05)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.RUNNING)
    mark = w.events.mark()
    w.phone.display = "ROTATION_90"
    end = time.monotonic() + 3
    seen = False
    while time.monotonic() < end and not seen:
        w.svc.grant_all()
        time.sleep(0.02)
        seen = any(e.warnings for e in w.events.since(mark))
    assert seen


def test_a_screen_switch_is_checked_soon_whatever_the_slow_pace(make: Callable[..., World]) -> None:
    """Screen2 opening (its Form resumes): two quick checks find the turned display long before the 3 s poll."""
    w = make(orient_check_s=60.0, orient_soon_s=0.05)
    rid = w.svc.submit(w.apk())
    running = w.stage_is(rid, S.RUNNING)
    assert running.pid is not None
    time.sleep(0.3)                                          # the launch's quick checks are over
    mark = w.events.mark()
    w.phone.display = "ROTATION_90"
    w.engine.current().push(LogEntry(now_us(), running.pid, running.pid, "I", "Form", "Form Screen2 got onResume"))
    w.events.wait(lambda e: e.warnings == (ErrorCode.APP_ORIENTATION_MISMATCH,), timeout_s=2, after=mark)


def test_a_turn_still_settling_is_not_a_mismatch(make: Callable[..., World]) -> None:
    w = make(orient_check_s=0.05, orient_soon_s=0.05)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.RUNNING)
    mark = w.events.mark()
    w.svc._orient_seen = None                                # noqa: SLF001
    w.phone.display = "ROTATION_90"
    time.sleep(0.07)                                         # one check sees it ...
    w.phone.display = "ROTATION_0"                           # ... and the display settles before the next
    time.sleep(0.3)
    assert not [e for e in w.events.since(mark) if e.warnings]


def test_newest_drop_wins_while_the_phone_boots(make: Callable[..., World]) -> None:
    engine = FakeEngine(ready=False)
    w = make(engine=engine)
    a = w.svc.submit(w.apk("A", "appinventor.ai_test.A"))
    w.stage_is(a, S.WAITING_ENGINE)
    b = w.svc.submit(w.apk("B", "appinventor.ai_test.B"))
    w.stage_is(a, S.CANCELLED)
    engine.new_epoch(EpochCause.BOOT)
    w.stage_is(b, S.RUNNING)
    assert [p for p, _ in w.phone.installs] == ["appinventor.ai_test.B"] and engine.starts == 0


def test_waiting_run_can_be_cancelled_and_a_stopped_engine_is_started(make: Callable[..., World]) -> None:
    engine = FakeEngine(ready=False)
    engine.st = replace(engine.st, state=EngineState.STOPPED)
    w = make(engine=engine)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.WAITING_ENGINE)
    until(lambda: engine.starts == 1)
    w.svc.cancel(rid)
    w.stage_is(rid, S.CANCELLED)


def test_engine_failure_stops_the_run(make: Callable[..., World]) -> None:
    engine = FakeEngine(ready=False)
    w = make(engine=engine)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.WAITING_ENGINE)
    engine.fail(messages_ko.error(ErrorCode.PRE_WHPX_OFF))
    ev = w.stage_is(rid, S.FAILED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.ENGINE_UNAVAILABLE
    assert "pre_whpx_off" in ev.problem.detail and not w.phone.installs


def test_incompatible_signature_asks_before_replacing(make: Callable[..., World]) -> None:
    failure = (f"Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package {PKG} signatures do not match "
               "newer version; ignoring!]")
    w = make()
    w.phone.install_outputs = [failure]
    rid = w.svc.submit(w.apk())
    ev = w.events.wait(lambda e: e.run_id == rid and e.prompt is not None)
    assert ev.prompt is not None and ev.prompt.choices == ("replace", "cancel")
    assert messages_ko.text(ev.prompt.text_key).startswith("다른 App Inventor 계정")
    w.svc.answer(rid, ev.prompt.prompt_id, "replace")
    w.stage_is(rid, S.RUNNING)
    assert w.phone.uninstalls == [PKG] and len(w.phone.installs) == 2
    other = make()
    other.phone.install_outputs = [failure]
    rid = other.svc.submit(other.apk())
    ev = other.events.wait(lambda e: e.run_id == rid and e.prompt is not None)
    assert ev.prompt is not None
    other.svc.answer(rid, ev.prompt.prompt_id, "cancel")
    other.stage_is(rid, S.CANCELLED)
    assert other.phone.uninstalls == []


def test_blocked_or_wrong_files_never_install(make: Callable[..., World]) -> None:
    w = make()
    ev = w.stage_is(w.svc.submit(w.apk("New", PKG, min_sdk=40)), S.FAILED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.INSTALL_OLDER_SDK and ev.issues
    ev = w.stage_is(w.svc.submit(REPO / "tests" / "fixtures" / "OrcusProbe.aia"), S.FAILED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.FILE_IS_AIA
    assert messages_ko.text(ev.problem.message_key)
    assert not w.phone.installs and len(list(w.staging.glob("*.apk"))) == 1   # the .aia copy is gone
    ev = w.stage_is(w.svc.submit(w.dir / "nowhere.apk"), S.FAILED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.STAGE_COPY_FAILED


def test_unreadable_manifest_finds_the_package_after_installing(make: Callable[..., World]) -> None:
    w = make()
    rid = w.svc.submit(w.apk("Mystery", "appinventor.ai_test.Mystery", unreadable=True))
    ev = w.stage_is(rid, S.RUNNING)
    assert ev.info is None and ev.issues == () and w.phone.running["appinventor.ai_test.Mystery"] == ev.pid
    assert any(c[:3] == ("cmd", "package", "resolve-activity") for c in w.phone.commands)


def test_logcat_problems_become_run_events(make: Callable[..., World]) -> None:
    w = make()
    form = ("Form Screen1 ErrorOccurred, errorNumber = 1501, componentType = BarcodeScanner, "
            "functionName = BarcodeScanner, messages = Your device does not have a scanning application installed.")
    # written while `am start` runs, before the PID is known: replayed to the watcher from the ring buffer
    w.phone.on_start = lambda pkg, pid: w.engine.current().push(LogEntry(now_us(), pid, pid, "E", "Form", form))
    rid = w.svc.submit(w.apk())
    running = w.stage_is(rid, S.RUNNING)
    ev = w.events.wait(lambda e: e.run_id == rid and e.problem is not None)
    assert ev.stage is S.RUNNING and ev.problem is not None and ev.problem.message_key == "app.error.1501"
    pid = running.pid
    assert pid is not None
    s = w.engine.current()
    t = now_us()
    for i, m in enumerate(["FATAL EXCEPTION: main", f"Process: {PKG}, PID: {pid}",
                           "java.lang.RuntimeException: boom", "\tat x.y(z.java:1)"]):
        s.push(LogEntry(t + i, pid, pid, "E", "AndroidRuntime", m))
    s.push(LogEntry(t + 10, SYSTEM_PID, SYSTEM_PID, "I", "ActivityManager", f"Process {PKG} (pid {pid}) has died"))
    ev = w.stage_is(rid, S.CRASHED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.APP_CRASH
    assert ev.problem.detail == "java.lang.RuntimeException: boom"


def test_no_process_reads_the_crash_buffer(make: Callable[..., World], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "PID_WAIT_S", 0.0)
    w = make()
    w.phone.no_process = True
    w.phone.crash_text = "\n".join(f"10-30 17:01:02.345  4001  4001 E AndroidRuntime: {m}" for m in (
        "FATAL EXCEPTION: main", f"Process: {PKG}, PID: 4001", "java.lang.IllegalStateException: early"))
    ev = w.stage_is(w.svc.submit(w.apk()), S.CRASHED)
    assert ev.problem is not None and ev.problem.detail == "java.lang.IllegalStateException: early"
    w.phone.crash_text = ""
    ev = w.stage_is(w.svc.submit(w.apk("B", "appinventor.ai_test.B")), S.EXITED)
    assert ev.problem is not None and ev.problem.code is ErrorCode.APP_DIED


def test_landscape_launcher_turns_the_phone_before_starting(make: Callable[..., World]) -> None:
    w = make()
    land = w.apk("Land", "appinventor.ai_test.Land", orientation="landscape")
    w.stage_is(w.svc.submit(land), S.RUNNING)
    controls = w.engine.current().controls
    assert controls.rotations == [Rotation.LANDSCAPE]
    w.stage_is(w.svc.submit(land), S.RUNNING, after=w.events.mark())
    assert controls.rotations == [Rotation.LANDSCAPE]   # already there
    port = w.apk("Port", "appinventor.ai_test.Port", orientation="portrait")
    w.stage_is(w.svc.submit(port), S.RUNNING, after=w.events.mark())
    assert controls.rotations == [Rotation.LANDSCAPE, Rotation.PORTRAIT]
    assert controls.gradual == [False, False]   # nothing listens before the launch: one step
    controls.rotate(Rotation.LANDSCAPE)         # [Rotate left] while the portrait app runs
    w.stage_is(w.svc.submit(port), S.RUNNING, after=w.events.mark())
    assert controls.rotations[-1] == Rotation.PORTRAIT   # the posture the buttons left, not a remembered one


def test_epoch_change_while_installing(make: Callable[..., World]) -> None:
    w = make()
    w.phone.on_install = lambda: w.engine.new_epoch(EpochCause.CRASH_RESTART)
    rid = w.svc.submit(w.apk())
    w.stage_is(rid, S.RUNNING, epoch=2)                 # the latest drop, once more in the new epoch
    assert len(w.phone.installs) == 2
    r = make()
    r.phone.on_install = lambda: r.engine.new_epoch(EpochCause.RESET)
    rid = r.svc.submit(r.apk())
    r.stage_is(rid, S.CANCELLED)
    assert len(r.phone.installs) == 1


def test_app_commands(make: Callable[..., World]) -> None:
    w = make()
    rid = w.svc.submit(w.apk(permissions=["android.permission.ACCESS_FINE_LOCATION",
                                          "android.permission.INTERNET"]))
    first = w.stage_is(rid, S.RUNNING)
    mark = w.events.mark()
    w.svc.grant_all()
    w.phone.display = "ROTATION_90"
    w.svc.match_orientation()
    w.svc.clear_data()
    second = w.stage_is(rid, S.RUNNING, after=mark)
    assert second.pid != first.pid and ("pm", "clear", PKG) in w.phone.commands
    assert ("pm", "grant", PKG, "android.permission.ACCESS_FINE_LOCATION") in w.phone.commands
    assert w.engine.current().controls.rotations == [Rotation.LANDSCAPE]   # display said ROTATION_90
    assert w.engine.current().controls.gradual == [True]                   # the app listens: 15° steps
    assert len(w.phone.installs) == 1                                      # clearing data keeps the record
    mark = w.events.mark()
    w.svc.stop_app()
    w.stage_is(rid, S.EXITED, after=mark)
    assert PKG not in w.phone.running
    mark = w.events.mark()
    w.svc.rerun()
    w.stage_is(rid, S.RUNNING, after=mark)
    mark = w.events.mark()
    w.svc.uninstall()
    w.stage_is(rid, S.EXITED, after=mark)
    assert PKG not in w.phone.packages
    mark = w.events.mark()
    w.svc.rerun()                                                          # installs again after uninstall
    w.stage_is(rid, S.RUNNING, after=mark)
    assert len(w.phone.installs) == 2


def test_subscribe_log_app_only(make: Callable[..., World]) -> None:
    w = make()
    app: list[LogEntry] = []
    everything: list[LogEntry] = []
    w.svc.subscribe_log(app.append)
    w.svc.subscribe_log(everything.append, app_only=False)
    ev = w.stage_is(w.svc.submit(w.apk()), S.RUNNING)
    assert ev.pid is not None
    s = w.engine.current()
    s.push(LogEntry(now_us(), ev.pid, ev.pid, "I", "Screen1", "from the app"))
    s.push(LogEntry(now_us(), 999, 999, "I", "Other", "someone else"))
    until(lambda: sum(e.tag in ("Screen1", "Other") for e in everything) == 2)
    assert [e.msg for e in app if e.tag in ("Screen1", "Other")] == ["from the app"]


def test_close_returns_promptly(make: Callable[..., World]) -> None:
    w = make(engine=FakeEngine(ready=False))
    w.stage_is(w.svc.submit(w.apk()), S.WAITING_ENGINE)
    t = time.monotonic()
    w.svc.close()
    assert time.monotonic() - t < 2.0
