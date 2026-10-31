"""ApkService (ARCHITECTURE.md §6.2, §7, §8): drops run one at a time on the `apk-pipeline` thread.

Every public method only enqueues. The newest drop wins: submit() cancels the running job's waits, and older
queued drops are skipped. The engine is reached through EngineControl only: `wait_ready`, then one epoch-bound
shell per attempt. An epoch change while installing or launching surfaces as EngineNotReady: after a crash
restart the latest drop runs again once in the new epoch; after a reset it is cancelled. The app that was
running is reinstalled after a crash restart only (§6.2). The same file in the same epoch skips `adb install`.

Logcat is one stream per epoch on the `apk-logcat` thread. Each launch gets a Watcher; lines that arrived
between `am start` and the PID are replayed to it from a ring buffer. Findings go back through the pipeline
queue, so every RunEvent is emitted on the pipeline thread.
"""

import queue
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orcus.common import log, messages_ko
from orcus.contracts import (
    ApkInfo,
    ApkSettings,
    CancellableIter,
    DeviceShell,
    EngineControl,
    EngineNotReady,
    EngineState,
    EngineStatus,
    EpochCause,
    ErrorCode,
    FileKind,
    Issue,
    IssueCode,
    IssueLevel,
    LogEntry,
    OrcusError,
    OrcusFailure,
    Prompt,
    Rotation,
    RunEvent,
    RunPolicy,
    RunStage,
    SessionMode,
)

from . import compat, install, intake, launch
from .inspect import inspect
from .watcher import Finding, Watcher, crash_from_buffer, line

_log = log.get("apk.service")
WAIT_READY_S = 900.0     # a first-time or rebuilding phone takes ~3.5 min before it boots (§6.5)
LOG_LIVE_WAIT_S = 3.0    # first line past the backlog cut: a guest clock behind the host would hide early lines
RING_LINES = 4000
REPORT_LINES = 50        # app log lines in [Copy report] (§7.9)
REPORT_WARN_LINES = 30
MAX_FILE = 1 << 30       # nothing App Inventor builds comes near 1 GiB: refuse before copying a dropped video
UNKNOWN_TARGET_SDK = 36  # unreadable manifest: install retries with the legacy flag by itself if needed
ORIENT_CHECK_S = 3.0     # while an app runs: its display rotation against the posture (§9.7, 0.06 s each)
ORIENT_SOON_S = 1.2      # right after a launch or a screen switch: two quick checks (the display settles in ~1 s)
RECORD_AUDIO = "android.permission.RECORD_AUDIO"
LOCATION = frozenset({"android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION"})


class _Cancelled(Exception):
    pass


@dataclass(eq=False)
class _Run:
    run_id: str
    path: Path
    policy: RunPolicy
    t0: float
    cancel: threading.Event = field(default_factory=threading.Event)
    info: ApkInfo | None = None
    issues: tuple[Issue, ...] = ()
    staged: Path | None = None
    sha: str = ""
    package: str | None = None
    launcher: str | None = None
    epoch: int = 0
    pid: int | None = None
    stage: RunStage = RunStage.STAGING
    problem: OrcusError | None = None
    restarted: bool = False
    warnings: tuple[ErrorCode, ...] = ()


@dataclass(eq=False)
class _Slot:
    run_id: str
    event: threading.Event = field(default_factory=threading.Event)
    choice: str = ""


class Service:
    """orcus.contracts.ApkService."""

    def __init__(self, engine: EngineControl, staging: Path, settings: Callable[[], ApkSettings], *,
                 mic_allowed: Callable[[str], bool] = lambda package: False,
                 sleep: Callable[[float], None] = time.sleep, orient_check_s: float = ORIENT_CHECK_S,
                 orient_soon_s: float = ORIENT_SOON_S) -> None:
        self._engine = engine
        self._staging = staging
        self._settings = settings
        self._mic_allowed = mic_allowed   # per-package consent from the settings (§9.8)
        self._sleep = sleep
        self._orient_check_s = orient_check_s
        self._orient_soon_s = orient_soon_s
        self._q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._lock = threading.Lock()            # submit/cancel/answer/subscribe vs the pipeline
        self._latest: str | None = None
        self._submits = 0   # drops so far: [Run again] pressed before a newer drop must not cancel it
        self._active: _Run | None = None
        self._cancelled: set[str] = set()
        self._prompts: dict[str, _Slot] = {}
        self._subs: list[Callable[[RunEvent], None]] = []
        self._log_subs: list[tuple[Callable[[LogEntry], None], bool]] = []
        # pipeline thread only
        self._current: _Run | None = None        # the app [Run again] and the app panel mean
        self._front: tuple[str, int] | None = None   # (package, epoch) last launched: force-stopped before the next
        self._installed: dict[str, str] = {}     # package -> sha installed by us in _installed_epoch (fast path)
        self._installed_epoch = -1
        self._orient_seen: tuple[Rotation, Rotation] | None = None   # last (display, posture) that differed
        self._next_check = 0.0
        self._quick_checks = 0   # quick ones still due after a launch or a screen switch
        # logcat thread + pipeline, under _watch_lock
        self._watch_lock = threading.Lock()
        self._stream: CancellableIter[LogEntry] | None = None
        self._stream_epoch: int | None = None
        self._stream_live = threading.Event()
        self._ring: deque[tuple[int, LogEntry]] = deque(maxlen=RING_LINES)
        self._seq = 0
        self._watch: Watcher | None = None
        self._watch_run: str | None = None
        self._gen = 0
        self._app: tuple[str, int] | None = None   # (package, pid) for subscribe_log(app_only=True)
        self._app_log: deque[str] = deque(maxlen=REPORT_LINES)          # I and above: App Inventor's Form logs
        self._app_warn: deque[str] = deque(maxlen=REPORT_WARN_LINES)    # D lines many times a second (S5)
        self._seen_epoch: int | None = None        # engine callback thread only
        self._unsubscribe = engine.subscribe(self._on_engine)
        self._thread = threading.Thread(target=self._loop, name="apk-pipeline", daemon=True)
        self._thread.start()
        self._on_engine(engine.status())

    # --- ApkService (any thread) --------------------------------------------------------------------------------
    def submit(self, path: Path, policy: RunPolicy | None = None) -> str:
        if policy is None:
            policy = RunPolicy.FRESH_PHONE if self._settings().reset_before_each_run else RunPolicy.KEEP_DATA
        run = _Run(uuid.uuid4().hex[:12], Path(path), policy, time.monotonic())
        with self._lock:
            self._submits += 1
            self._latest = run.run_id
            if self._active is not None:
                self._active.cancel.set()   # the newest drop wins
        self._q.put(("run", run))
        return run.run_id

    def answer(self, run_id: str, prompt_id: str, choice: str) -> None:
        with self._lock:
            slot = self._prompts.get(prompt_id)
        if slot is not None and slot.run_id == run_id:
            slot.choice = choice
            slot.event.set()

    def cancel(self, run_id: str) -> None:
        with self._lock:
            self._cancelled.add(run_id)
            if self._active is not None and self._active.run_id == run_id:
                self._active.cancel.set()

    def rerun(self) -> None:
        with self._lock:
            self._q.put(("rerun", self._submits))

    def stop_app(self) -> None:
        self._q.put(("stop_app", None))

    def clear_data(self) -> None:
        with self._lock:
            self._q.put(("clear_data", self._submits))

    def uninstall(self) -> None:
        self._q.put(("uninstall", None))

    def grant_all(self) -> None:
        self._q.put(("grant_all", None))

    def match_orientation(self) -> None:
        self._q.put(("match_orientation", None))

    def subscribe(self, cb: Callable[[RunEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def unsubscribe() -> None:
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return unsubscribe

    def subscribe_log(self, cb: Callable[[LogEntry], None], *, app_only: bool = True) -> Callable[[], None]:
        item = (cb, app_only)
        with self._lock:
            self._log_subs.append(item)

        def unsubscribe() -> None:
            with self._lock:
                if item in self._log_subs:
                    self._log_subs.remove(item)
        return unsubscribe

    def report_lines(self) -> list[str]:
        """[Copy report] lines about the current app (§7.9). Any thread: a slightly stale view is fine."""
        run = self._current
        if run is None:
            return ["앱: 없음"]
        info = run.info
        if info is not None:
            lines = [f"앱: {info.label} ({info.package}) {info.version_name or '-'} ({info.version_code}) · "
                     f"minSdk {info.min_sdk} · targetSdk {info.target_sdk} · {info.ai_era or '시기 모름'} · "
                     f"방향 {info.launcher_orientation or '-'}"]
        else:
            lines = [f"앱: {run.package or run.path.name} (매니페스트를 읽지 못함)"]
        lines.append(f"실행: {run.stage} · epoch {run.epoch} · pid {run.pid or '-'} · SHA-256 {run.sha[:16]}")
        lines += [f"점검: {i.level} {i.code}" for i in run.issues]
        if run.problem is not None:
            lines.append(f"문제: {run.problem.code} {run.problem.detail}")
        with self._watch_lock:
            log_lines, warn_lines = list(self._app_log), list(self._app_warn)
        return [*lines, f"--- 앱 경고·오류 최근 {len(warn_lines)}줄 ---", *warn_lines,
                f"--- 앱 로그 최근 {len(log_lines)}줄 (I 이상) ---", *log_lines]

    def close(self, *, fast: bool = False) -> None:
        """Shutdown step 2 (§4): cancel the pipeline and the log stream. Fast (logoff): do not wait for an install
        or launch in flight; the thread is a daemon and the engine's stop ends its adb calls (3a review)."""
        self._unsubscribe()
        with self._lock:
            self._latest = None
            if self._active is not None:
                self._active.cancel.set()
        self._q.put(("close", None))
        self._thread.join(0.2 if fast else 5)
        with self._watch_lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.cancel()

    # --- engine callback (supervisor thread: enqueue only) -------------------------------------------------------
    def _on_engine(self, st: EngineStatus) -> None:
        if st.state == EngineState.READY and st.epoch != self._seen_epoch:
            self._seen_epoch = st.epoch
            self._q.put(("epoch", st))

    # --- pipeline thread ------------------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            run = self._current
            wait: float | None = None
            if run is not None and run.stage == RunStage.RUNNING:
                wait = self._next_check - time.monotonic()
                if wait <= 0:   # by the clock: commands arriving more often than that must not starve it
                    self._quick_checks = max(0, self._quick_checks - 1)
                    self._next_check = time.monotonic() + (self._orient_soon_s if self._quick_checks
                                                           else self._orient_check_s)
                    try:
                        self._check_orientation()
                    except Exception:
                        _log.exception("orientation check failed")
                    continue
            try:
                cmd, arg = self._q.get(timeout=wait)
            except queue.Empty:
                continue
            if cmd == "close":
                return
            try:
                match cmd:
                    case "run":
                        self._job(arg)
                    case "epoch":
                        self._on_epoch(arg)
                    case "finding":
                        self._on_finding(*arg)
                    case _:
                        self._app_command(cmd, arg)
            except Exception:
                _log.exception("%s failed", cmd)

    def _job(self, run: _Run) -> None:
        with self._lock:
            stale = run.run_id != self._latest or run.run_id in self._cancelled
            if not stale:
                self._active = run
        if stale:
            self._emit(run, RunStage.CANCELLED)
            return
        try:
            self._prepare(run)
            self._deliver(run)
        except _Cancelled:
            self._emit(run, RunStage.CANCELLED)
        except OrcusFailure as f:
            self._emit(run, RunStage.FAILED, problem=f.error)
        except Exception as e:   # any other error still ends the run: the GUI's busy overlay waits for it (3a review)
            _log.exception("run %s", run.run_id)
            self._emit(run, RunStage.FAILED, problem=messages_ko.error(ErrorCode.STAGE_UNEXPECTED, repr(e)[:300]))
        finally:
            with self._lock:
                self._active = None

    def _prepare(self, run: _Run) -> None:
        """STAGING -> INSPECTING (the app card) -> CHECKING (§8 steps 2-5)."""
        self._emit(run, RunStage.STAGING)
        try:
            too_big = run.path.stat().st_size > MAX_FILE
        except OSError as e:
            raise OrcusFailure(messages_ko.error(ErrorCode.STAGE_COPY_FAILED, f"{run.path}: {e}")) from e
        if too_big:
            raise OrcusFailure(messages_ko.error(ErrorCode.FILE_NOT_ANDROID, run.path.name))
        # copy first, then read: an online-only Drive file downloads when read, and only the copy has a deadline
        run.sha, run.staged = intake.stage(run.path, self._staging, keep=self._settings().staging_keep)
        kind = intake.classify(run.staged)
        if kind != FileKind.APK:
            run.staged.unlink(missing_ok=True)
            raise OrcusFailure(messages_ko.error(intake.kind_error(kind) or ErrorCode.FILE_NOT_ANDROID,
                                                 run.path.name))
        self._check(run)
        try:
            run.info = inspect(run.staged, run.sha)
        except Exception as e:   # never blocks: the package is found after installing instead (§7.3)
            _log.warning("manifest unreadable (%s): %s", run.path.name, e)
        if run.info is not None and run.info.package:
            run.package, run.launcher = run.info.package, run.info.launcher
        self._emit(run, RunStage.INSPECTING)
        issues = compat.preflight(run.info, self._engine.status().caps) if run.info is not None else []
        run.issues = tuple(issues)
        self._emit(run, RunStage.CHECKING)
        block = compat.blocking(issues)
        if block is not None:
            raise OrcusFailure(messages_ko.error(ErrorCode(block.code), run.path.name))
        self._check(run)

    def _deliver(self, run: _Run) -> None:
        """WAITING_ENGINE -> INSTALLING -> LAUNCHING -> RUNNING (§8 steps 6-10)."""
        self._current = run
        policy = run.policy
        self._emit(run, RunStage.WAITING_ENGINE)
        if self._engine.status().state == EngineState.STOPPED:
            self._engine.start()
        st = self._wait(run)
        if policy == RunPolicy.FRESH_PHONE and st.mode == SessionMode.COLD:
            _log.info("COLD mode: a fresh phone would be a rebuild of minutes, running as FRESH_APP (§6.2)")
            policy = RunPolicy.FRESH_APP
            run.issues = (*run.issues, Issue(IssueLevel.INFO, IssueCode.COLD_FRESH_APP, "issue.cold_fresh_app"))
        elif policy == RunPolicy.FRESH_PHONE:
            st = self._wait(run, min_epoch=self._engine.reset_device())
        run.epoch = st.epoch
        try:
            self._ensure_logcat(st.epoch)   # the stream's backlog drains while installing (a 3 s wait otherwise)
        except EngineNotReady:
            pass                            # handled by the attempt below
        while True:
            try:
                shell = self._engine.shell()
                run.epoch = shell.epoch
                self._install(run, shell, policy)
                self._launch(run, shell)
                return
            except EngineNotReady as e:
                if run.restarted or not self._is_latest(run):
                    raise _Cancelled() from e
                _log.info("phone left epoch %d during %s: %s", run.epoch, run.stage, e)
                st = self._wait(run, min_epoch=run.epoch + 1)
                if st.epoch_cause != EpochCause.CRASH_RESTART:
                    raise _Cancelled() from e
                run.restarted = True   # the latest drop, once more in the new epoch (§6.2)
                self._emit(run, RunStage.WAITING_ENGINE)

    def _wait(self, run: _Run, *, min_epoch: int | None = None) -> EngineStatus:
        try:
            return self._engine.wait_ready(WAIT_READY_S, min_epoch=min_epoch, cancel=run.cancel)
        except CancelledError as e:
            raise _Cancelled() from e
        except OrcusFailure as f:   # the engine panel shows the cause; this run just stops (§8 step 6)
            raise OrcusFailure(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE,
                                                 f"{f.error.code}: {f.error.detail}")) from f

    def _install(self, run: _Run, shell: DeviceShell, policy: RunPolicy) -> None:
        if self._installed_epoch != shell.epoch:
            self._installed.clear()
            self._installed_epoch = shell.epoch
        self._stop_front(shell)
        if policy == RunPolicy.KEEP_DATA and run.package and self._installed.get(run.package) == run.sha:
            _log.info("%s: same file in epoch %d, install skipped", run.package, shell.epoch)
            return
        assert run.staged is not None
        self._emit(run, RunStage.INSTALLING)
        if policy != RunPolicy.KEEP_DATA and run.package:
            shell.uninstall(run.package)
        before = install.third_party(shell) if run.package is None else frozenset[str]()
        target = run.info.target_sdk if run.info is not None else UNKNOWN_TARGET_SDK
        grant = self._settings().grant_all
        out = install.install(shell, run.staged, grant_all=grant, target_sdk=target)
        if out.needs_uninstall_prompt:
            # only the package we parsed ourselves: adb's output can echo text a crafted manifest put there (3a)
            pkg = run.package
            if pkg is None:
                raise OrcusFailure(messages_ko.error(ErrorCode.INSTALL_INCOMPATIBLE, out.detail))
            if self._ask(run, "error.install_incompatible", ("replace", "cancel")) != "replace":
                raise _Cancelled()
            shell.uninstall(pkg)
            self._installed.pop(pkg, None)
            out = install.install(shell, run.staged, grant_all=grant, target_sdk=target)
        if not out.ok:
            raise OrcusFailure(messages_ko.error(out.code or ErrorCode.INSTALL_INCOMPATIBLE, out.detail))
        if run.package is None:
            run.package = install.installed_package(before, install.third_party(shell))
            if run.package is None:
                raise OrcusFailure(messages_ko.error(ErrorCode.APK_PARSE, "package unknown after install"))
        self._installed[run.package] = run.sha
        _log.info("%s installed in %.1f s (epoch %d)", run.package, out.seconds, shell.epoch)

    def _launch(self, run: _Run, shell: DeviceShell) -> None:
        assert run.package is not None
        run.pid = None
        started = time.monotonic()
        self._emit(run, RunStage.LAUNCHING)
        self._turn_for(run)
        self._apply_mic(run)
        self._ensure_logcat(shell.epoch).wait(LOG_LIVE_WAIT_S)
        with self._watch_lock:
            mark = self._seq
        out = launch.launch(shell, run.package, run.launcher, sleep=self._sleep)
        self._front = (run.package, shell.epoch)
        if not out.ok:
            raise OrcusFailure(messages_ko.error(out.code or ErrorCode.APP_LAUNCH_NOT_FOUND, out.detail))
        if out.pid is None:   # gone within 5 s: the crash buffer has the reason (§7.6 step 4)
            err = crash_from_buffer(launch.crash_buffer(shell), run.package)
            if err is None:
                self._emit(run, RunStage.EXITED,
                           problem=messages_ko.error(ErrorCode.APP_DIED, "no process 5 s after am start"))
            else:
                self._emit(run, RunStage.CRASHED, problem=err)
            return
        run.pid = out.pid
        self._attach(run, mark)
        self._emit(run, RunStage.RUNNING)
        self._screen_shown(run)
        _log.info("%s running %.1f s after the drop (launching %.1f s, am start %s ms)", run.package,
                  time.monotonic() - run.t0, time.monotonic() - started, out.total_ms)

    def _turn_for(self, run: _Run) -> None:
        """Pre-rotation (§7.6 step 2) from the device's posture (the rotate buttons turn it too; a new epoch starts
        in portrait). Nothing listens to the sensors yet, so the turn is one step."""
        controls = self._engine.session().controls
        if controls is None:
            return
        want = launch.wanted_rotation(run.info.launcher_orientation if run.info else None, controls.posture)
        if want is not None:
            controls.rotate(want, gradual=False)

    def _apply_mic(self, run: _Run) -> None:
        """§9.8: the microphone is on only for an app the user turned it on for (remembered per package). Every
        launch sets it, so a crash restart re-applies it and the next app does not inherit it."""
        controls = self._engine.session().controls
        if controls is None:
            return
        wanted = bool(run.package) and self._mic_allowed(run.package or "")
        if wanted != controls.microphone:
            controls.set_microphone(wanted)

    def _screen_shown(self, run: _Run) -> None:
        """A launch or an App Inventor screen coming back (Screen2, the permission dialog closing): the display
        may have turned, and a LocationSensor starts listening. The polls stay at 3 s otherwise (B9: each check
        is an adb process, 0.6% of a core every 2 s in 3b)."""
        self._quick_checks = 2
        self._next_check = min(self._next_check, time.monotonic() + self._orient_soon_s)
        self._relocate(run)

    def _relocate(self, run: _Run) -> None:
        """A LocationSensor asks for updates when its screen starts or resumes (also after the permission dialog),
        and a test provider reports only when told: send the phone's location again for the first fix (3b device
        test: the app showed no location until a preset was chosen)."""
        if run.info is None or not run.info.permissions & LOCATION:
            return
        controls = self._engine.session().controls
        if controls is not None:
            controls.resend_location()

    def _check_orientation(self) -> None:
        """A landscape Screen2, a scanner or a ScreenOrientation block turns the display alone: the frame follows
        the posture and shows the app turned (S7). Banner when (display, posture) differ in two checks in a row:
        the display settles about a second after a turn."""
        run = self._current
        if run is None or run.stage != RunStage.RUNNING or self._engine.status().state != EngineState.READY:
            return   # resetting or restarting: the next epoch decides about the run
        try:
            shell = self._engine.shell()
            controls = self._engine.session().controls
            if controls is None or shell.epoch != run.epoch:
                return
            display = launch.display_rotation(shell)
        except OrcusFailure as e:
            _log.info("orientation check skipped: %s", e)
            return
        seen = (display, controls.posture) if display is not None and display != controls.posture else None
        mismatch = seen is not None and seen == self._orient_seen
        self._orient_seen = seen
        if mismatch != (ErrorCode.APP_ORIENTATION_MISMATCH in run.warnings):
            _log.info("orientation %s: display %s, posture %s", "differs" if mismatch else "matches again",
                      display, controls.posture)
            run.warnings = (ErrorCode.APP_ORIENTATION_MISMATCH,) if mismatch else ()
            self._emit(run, run.stage)

    def _stop_front(self, shell: DeviceShell) -> None:
        self._detach()
        if self._front is not None and self._front[1] == shell.epoch:
            launch.force_stop(shell, self._front[0])

    def _ask(self, run: _Run, text_key: str, choices: tuple[str, ...]) -> str:
        prompt = Prompt(run.run_id, uuid.uuid4().hex[:8], text_key, choices)
        slot = _Slot(run.run_id)
        with self._lock:
            self._prompts[prompt.prompt_id] = slot
        try:
            self._emit(run, run.stage, prompt=prompt)
            while not slot.event.wait(0.1):
                self._check(run)
            return slot.choice
        finally:
            with self._lock:
                self._prompts.pop(prompt.prompt_id, None)

    def _check(self, run: _Run) -> None:
        if run.cancel.is_set():
            raise _Cancelled()

    def _is_latest(self, run: _Run) -> bool:
        with self._lock:
            return self._latest == run.run_id

    def _on_epoch(self, st: EngineStatus) -> None:
        """§6.2: only a crash restart reinstalls the running app; reset and rebuild leave the phone clean."""
        try:
            self._ensure_logcat(st.epoch)
        except OrcusFailure as e:
            _log.info("no logcat in epoch %d: %s", st.epoch, e)
        run = self._current
        if run is None or run.epoch >= st.epoch or run.stage != RunStage.RUNNING:
            return
        self._detach()
        if st.epoch_cause == EpochCause.CRASH_RESTART and self._is_latest(run):
            _log.info("phone restarted after a crash: reinstalling %s", run.package)
            self._again(run)
        else:
            self._emit(run, RunStage.EXITED)   # reset/rebuild/new boot: the app is gone with the old phone

    def _on_finding(self, run_id: str | None, gen: int, found: Finding) -> None:
        with self._watch_lock:
            live = gen == self._gen
        run = self._current
        if not live or run is None or run.run_id != run_id:
            return
        stage = RunStage.RUNNING
        if found.ended:
            crashed = found.error.code in (ErrorCode.APP_CRASH, ErrorCode.APP_SPEECH_MISSING)
            stage = RunStage.CRASHED if crashed else RunStage.EXITED
            self._detach()
        self._emit(run, stage, problem=found.error)

    def _again(self, run: _Run, *, clear: bool = False) -> None:
        """[Run again], [Clear data and run], crash-restart reinstall: KEEP_DATA, reinstalling only when this
        epoch has not got the file (after [Reset phone], say)."""
        run.cancel = threading.Event()   # before it is published: a cancel in between must not be lost
        run.t0 = time.monotonic()
        with self._lock:
            self._latest = run.run_id
            self._active = run
        try:
            shell = self._engine.shell()
            run.epoch = shell.epoch
            self._install(run, shell, RunPolicy.KEEP_DATA)
            if clear and run.package:
                launch.clear_data(shell, run.package)
            self._launch(run, shell)
        except _Cancelled:
            self._emit(run, RunStage.CANCELLED)
        except OrcusFailure as f:
            self._emit(run, RunStage.FAILED, problem=f.error)
        except Exception as e:   # as in _job: the run must reach a final stage
            _log.exception("run again %s", run.run_id)
            self._emit(run, RunStage.FAILED, problem=messages_ko.error(ErrorCode.STAGE_UNEXPECTED, repr(e)[:300]))
        finally:
            with self._lock:
                self._active = None

    def _app_command(self, cmd: str, submits: int | None = None) -> None:
        run = self._current
        if run is None or run.staged is None:
            return
        if cmd in ("rerun", "clear_data"):
            with self._lock:
                newer = submits is not None and submits != self._submits
            if newer:   # a file dropped after this press wins; running the old app now would cancel it (3a review)
                _log.info("%s skipped: a newer drop is queued", cmd)
                return
            self._again(run, clear=cmd == "clear_data")
            return
        if run.package is None:
            return
        try:
            shell = self._engine.shell()
            match cmd:
                case "stop_app":
                    self._detach()
                    launch.force_stop(shell, run.package)
                    self._emit(run, RunStage.EXITED)
                case "uninstall":
                    self._detach()
                    shell.uninstall(run.package)
                    self._installed.pop(run.package, None)
                    if self._front is not None and self._front[0] == run.package:
                        self._front = None
                    self._emit(run, RunStage.EXITED)
                case "grant_all":
                    if run.info is not None:
                        n = launch.grant_all(shell, run.package, run.info.permissions)
                        _log.info("%s: granted %d of %d permissions", run.package, n, len(run.info.permissions))
                case "screen_shown":
                    self._screen_shown(run)
                case "match_orientation":   # the app listens: a gradual turn, or it is caught as a shake (§9.7)
                    want = launch.display_rotation(shell)
                    controls = self._engine.session().controls
                    if want is not None and controls is not None and want != controls.posture:
                        controls.rotate(want)
                case _:
                    _log.warning("unknown command %s", cmd)
        except EngineNotReady as e:
            _log.info("%s ignored: %s", cmd, e)

    def _emit(self, run: _Run, stage: RunStage, *, problem: OrcusError | None = None,
              prompt: Prompt | None = None) -> None:
        run.stage = stage
        if problem is not None:
            run.problem = problem
        if stage != RunStage.RUNNING:
            run.warnings = ()
            self._orient_seen = None
        ev = RunEvent(run.run_id, stage, run.info, run.issues, problem, prompt,
                      round(time.monotonic() - run.t0, 3), run.pid, run.epoch, run.warnings)
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                _log.exception("run event subscriber failed")

    # --- logcat (pipeline starts it; the apk-logcat thread feeds it) --------------------------------------------
    def _ensure_logcat(self, epoch: int) -> threading.Event:
        with self._watch_lock:
            if self._stream is not None and self._stream_epoch == epoch:
                return self._stream_live
        session = self._engine.session()
        if session.epoch != epoch:
            raise EngineNotReady(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE,
                                                   f"session epoch {session.epoch}, wanted {epoch}"))
        stream = session.logcat()
        live = threading.Event()
        with self._watch_lock:
            old, self._stream = self._stream, stream
            self._stream_epoch, self._stream_live = epoch, live
            self._ring.clear()
        if old is not None:
            old.cancel()
        threading.Thread(target=self._consume, args=(stream, live), name="apk-logcat", daemon=True).start()
        return live

    def _consume(self, stream: CancellableIter[LogEntry], live: threading.Event) -> None:
        try:
            for entry in stream:
                live.set()
                with self._watch_lock:
                    if self._stream is not stream:
                        return
                    self._seq += 1
                    self._ring.append((self._seq, entry))
                    found = self._watch.feed(entry) if self._watch is not None else []
                    run_id, gen, app = self._watch_run, self._gen, self._app
                    if app is not None and (entry.pid == app[1] or app[0] in entry.msg) and entry.level not in "VD":
                        self._app_log.append(line(entry))
                        if entry.level in "WEF":
                            self._app_warn.append(line(entry))
                    resumed = (app is not None and entry.pid == app[1] and entry.tag == "Form"
                               and "got onResume" in entry.msg)   # App Inventor's Form, whenever a screen comes back
                if resumed:
                    self._q.put(("screen_shown", None))
                for f in found:
                    self._q.put(("finding", (run_id, gen, f)))
                self._publish_log(entry, app)
        except OrcusFailure as e:
            _log.warning("logcat stream failed: %s", e)
        finally:
            live.set()
            with self._watch_lock:
                found = []
                run_id, gen = self._watch_run, self._gen
                if self._stream is stream:
                    self._stream = self._stream_epoch = None
                    found = self._watch.flush() if self._watch is not None else []
            for f in found:
                self._q.put(("finding", (run_id, gen, f)))

    def _attach(self, run: _Run, mark: int) -> None:
        assert run.package is not None and run.pid is not None
        w = Watcher(run.package, run.pid, self._engine.status().caps)
        with self._watch_lock:
            self._gen += 1
            found = [f for seq, e in self._ring if seq > mark for f in w.feed(e)]
            self._watch, self._watch_run, self._app = w, run.run_id, (run.package, run.pid)
            gen = self._gen
        for f in found:
            self._q.put(("finding", (run.run_id, gen, f)))

    def _detach(self) -> None:
        with self._watch_lock:
            self._watch = self._watch_run = self._app = None
            self._gen += 1

    def _publish_log(self, entry: LogEntry, app: tuple[str, int] | None) -> None:
        with self._lock:
            subs = list(self._log_subs)
        for cb, app_only in subs:
            if app_only and (app is None or (entry.pid != app[1] and app[0] not in entry.msg)):
                continue
            try:
                cb(entry)
            except Exception:
                _log.exception("log subscriber failed")
