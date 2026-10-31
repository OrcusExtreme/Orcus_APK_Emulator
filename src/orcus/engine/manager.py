"""EngineManager (ARCHITECTURE.md §5.1): the VM lifecycle state machine behind EngineControl.

Threads (§4):
- engine-supervisor: the only thread that changes state. Takes API commands and job results from a queue,
  ticks once a second (disk check).
- engine-worker: one long job at a time (bring-up, provisioning, reset, teardown) with a cancel Event; it posts
  progress ("enter" a state, tagged with its job id) and one outcome back to the supervisor.
- engine-procwait / engine-keepalive: watch the READY emulator, post events.

Ownership: a job that launched an emulator tears it down itself when it fails or is cancelled; only a READY
emulator is handed to the supervisor (`_Ready.vm`), which tears it down through a teardown job.
Everything outside Python goes through `Seams`, so the unit tests run this machine on fakes.
"""

import os
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

import grpc
from google.protobuf import empty_pb2

from orcus.common import childenv, jsonio, log, messages_ko, pins, winjob
from orcus.common.paths import GOLDEN_SNAPSHOT, OrcusPaths
from orcus.common.ports import Ports
from orcus.contracts import (DeviceCaps, DeviceEndpoint, DeviceSession, DeviceSessionFactory, EngineState,
                             EngineStatus, EpochCause, ErrorCode, GpuMode, OrcusError, OrcusFailure, SessionMode,
                             Settings)
from orcus.emugrpc import channel as grpc_channel
from orcus.emugrpc.generated import emulator_controller_pb2 as ec
from orcus.emugrpc.generated import snapshot_service_pb2 as ss

from . import args, discovery, exitlog, gpu, provision, snapshots
from .adb import AdbLogcat, AdbServer, EpochShell, RawShell, client, not_ready
from .bootcheck import accelerator_from_log
from .discovery import DiscoveryRecord
from .seams import ProcessHandle, Seams

_log = log.get("engine.manager")
S = EngineState

GIB = 1 << 30
DISK_RUN_MIN = 6 * GIB
DISK_PROVISION_MIN = 10 * GIB
DISK_WARN = 3 * GIB
DISK_STOP = 1 * GIB
DISK_EVERY_S = 60.0
CONNECT_FIRST_S, CONNECT_S = 60.0, 30.0
BOOT_GOLDEN_S, BOOT_COLD_S = 30.0, 240.0
PROVISION_LIMIT_S = 600.0
SETTLE_BEFORE_SAVE_S = 10.0
ADB_DEVICE_S = 10.0
FRAME_CHECK_S = 10.0
POST_LOAD_HEALTH_S = 60.0          # §6.5 step 10: after a load, past S4's 20-25 s freeze and the Watchdog's 60 s
QUIET_BUSY = 0.35                  # 7a: save once guest CPU is below this busy share twice in a row...
QUIET_WAIT_S = 180.0               # ...or after this long anyway (logged). A golden saved at load ~12 came
                                   # back with system_server killed by its Watchdog (Phase 3a)
PAUSE_S, PAUSE_FAST_S = 3.0, 1.0   # §5.1 STOPPING: pause, then end qemu (its exit path can fail fast, 3a)
EMULATOR_IMAGES = discovery.EMULATOR_IMAGES
CRASH_WINDOW_S = 300.0
KEEPALIVE_FAILS = 3
DEFAULT_LOCATION = (37.5665, 126.9780)

ACTIVE = frozenset({S.PREFLIGHT, S.LAUNCHING, S.CONNECTING, S.BOOTING, S.SESSION_INIT, S.RESETTING,
                    S.PROVISIONING})
IDLE = frozenset({S.STOPPED, S.FAILED, S.NOT_INSTALLED})


@dataclass
class _Vm:
    """One running emulator."""

    mode: args.LaunchMode
    ports: Ports
    proc: ProcessHandle
    log_path: Path
    record: DiscoveryRecord | None = None
    channel: grpc.Channel | None = None
    stub: Any = None
    snap: Any = None
    shell: RawShell | None = None
    nonce: str = ""
    booted_at: float = 0.0
    session: DeviceSession | None = None
    watched: bool = False
    done: threading.Event = field(default_factory=threading.Event)   # set at teardown: ends the watchers

    def alive(self) -> bool:
        return self.proc.poll() is None


# --- job outcomes (worker -> supervisor) ----------------------------------------------------------------------
@dataclass
class _Outcome:
    pass


@dataclass
class _Ready(_Outcome):
    vm: _Vm
    caps: DeviceCaps
    warnings: tuple[ErrorCode, ...]
    epoch: int
    cause: EpochCause


@dataclass
class _Fail(_Outcome):
    error: OrcusError


@dataclass
class _GoldenBad(_Outcome):
    detail: str
    gpu_changed: bool = False   # the emulator deleted the golden itself: 'host GPU has changed' (3a finding 18)


@dataclass
class _NeedProvision(_Outcome):
    mode: SessionMode
    rebuild: bool


@dataclass
class _Retry(_Outcome):
    reason: str


@dataclass
class _Provisioned(_Outcome):
    record: snapshots.Record


@dataclass
class _ResetFailed(_Outcome):
    detail: str


@dataclass
class _Stopped(_Outcome):
    pass


@dataclass
class _AdbBack(_Outcome):
    vm: _Vm


@dataclass
class _AdbGone(_Outcome):
    error: OrcusError


class _Abort(Exception):
    """Worker side: end the job with this outcome."""

    def __init__(self, outcome: _Outcome) -> None:
        super().__init__(type(outcome).__name__)
        self.outcome = outcome


def _fail(code: ErrorCode, detail: str = "", excerpt: str = "") -> _Abort:
    return _Abort(_Fail(messages_ko.error(code, detail, excerpt)))


class EngineManager:
    """EngineControl implementation. Construct, `start()`, and `close()` once when the app exits."""

    def __init__(self, paths: OrcusPaths, settings: Callable[[], Settings], seams: Seams,
                 session_factory: DeviceSessionFactory | None = None, *, owner_kind: str = "gui") -> None:
        self._paths = paths
        self._settings = settings
        self._seams = seams
        self._clock = seams.clock
        self._factory = session_factory
        self._owner_kind = owner_kind
        self._q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self._lock = winjob.ExclusiveLock(paths.state_file("engine.lock"))
        self._cond = threading.Condition()
        self._subs: list[Callable[[EngineStatus], None]] = []
        self._subs_lock = threading.Lock()
        self._owner: dict[str, Any] | None = None   # engine_owner.json as written (worker at launch, supervisor after)
        self._owner_lock = threading.Lock()
        self._adb: AdbServer | None = None
        # worker-owned
        self._provision_id = ""
        self._verify: snapshots.Record | None = None   # set by the supervisor before the verification boot
        self._first_connect = True
        self._job_now = 0
        # supervisor-owned
        self._state = S.NOT_INSTALLED if self._missing_install() else S.STOPPED
        self._mode = SessionMode.GOLDEN
        self._epoch = 0
        self._cause = EpochCause.BOOT
        self._error: OrcusError | None = None
        self._warnings: tuple[ErrorCode, ...] = ()
        self._caps: DeviceCaps | None = None
        self._text_key = f"engine.state.{self._state}"
        self._progress: float | None = None
        self._vm: _Vm | None = None
        self._intent: tuple[str, Any] = ("stop", None)   # STOPPING exit: stop | restart | rebuild | fail
        self._next_cause = EpochCause.BOOT
        self._job: tuple[int, threading.Event] | None = None
        self._job_seq = 0
        self._rebuilds_used = 0          # failure rebuilds this run (limit 1, §5.1)
        self._crashes: deque[float] = deque()
        self._retried: set[str] = set()
        self._last_disk = -DISK_EVERY_S
        self._wanted = False             # a start is pending or the engine should be running
        self._status = self._make_status()
        self._worker_q: "queue.Queue[tuple[int, Callable[[threading.Event], _Outcome], threading.Event] | None]" = \
            queue.Queue()
        self._sup = threading.Thread(target=self._supervise, name="engine-supervisor", daemon=True)
        self._worker = threading.Thread(target=self._work, name="engine-worker", daemon=True)
        self._sup.start()
        self._worker.start()

    # --- EngineView / EngineControl (any thread) ----------------------------------------------------------------
    def status(self) -> EngineStatus:
        return self._status

    def start(self) -> None:
        self._wanted = True
        self._q.put(("start", None))

    def stop(self, *, fast: bool = False) -> None:
        self._wanted = False
        self._q.put(("stop", fast))

    def reset_device(self) -> int:
        target = self._status.epoch + 1
        self._q.put(("reset", None))
        return target

    def rebuild(self, reason: str) -> None:
        _log.info("rebuild requested: %s", reason)
        self._wanted = True
        self._q.put(("rebuild", reason))

    def retry(self) -> None:
        """[Retry] on the FAILED panel: also resets the failure-rebuild budget (§5.1)."""
        self._wanted = True
        self._q.put(("retry", None))

    def subscribe(self, cb: Callable[[EngineStatus], None]) -> Callable[[], None]:
        with self._subs_lock:
            self._subs.append(cb)

        def unsubscribe() -> None:
            with self._subs_lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return unsubscribe

    def wait_ready(self, timeout_s: float, *, min_epoch: int | None = None,
                   cancel: threading.Event | None = None) -> EngineStatus:
        assert threading.current_thread() is not self._sup, "wait_ready on the supervisor thread"
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while True:
                st = self._status
                if st.state == S.READY and (min_epoch is None or st.epoch >= min_epoch):
                    return st
                if st.state == S.FAILED and st.error is not None:
                    raise OrcusFailure(st.error)
                if st.state == S.NOT_INSTALLED:
                    raise OrcusFailure(messages_ko.error(ErrorCode.PRE_NOT_INSTALLED))
                if st.state == S.STOPPED and not self._wanted:
                    raise OrcusFailure(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE, "engine stopped"))
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OrcusFailure(messages_ko.error(ErrorCode.ENGINE_BOOT_TIMEOUT, "wait_ready timed out"))
                self._cond.wait(min(remaining, 0.25))

    def shell(self) -> EpochShell:
        st = self._status
        vm = self._vm
        if st.state != S.READY or vm is None or self._adb is None:
            raise not_ready(f"engine is {st.state}")
        return EpochShell(self._seams.adb, self._adb.port, vm.ports.serial, st.epoch, self._guard)

    def session(self) -> DeviceSession:
        vm = self._vm
        if self._status.state != S.READY or vm is None or vm.session is None:
            raise not_ready(f"engine is {self._status.state}")
        return vm.session

    def logcat(self) -> AdbLogcat:
        """The guest's log through adb from now on. The app hands it to each DeviceSession as its logcat source:
        gRPC streamLogcat goes silent after a snapshot load. The session cancels it when it closes."""
        vm = self._vm
        if self._status.state != S.READY or vm is None or self._adb is None or self._seams.adb_lines is None:
            raise not_ready(f"engine is {self._status.state}")
        return AdbLogcat(self._seams.adb_lines(client(self._adb.port, vm.ports.serial, *AdbLogcat.ARGS)))

    def close(self, timeout_s: float = 15.0) -> None:
        """App exit: fast stop if needed, end the threads, then stop the private adb server."""
        if self._status.state not in IDLE:
            self.stop(fast=True)
            end = time.monotonic() + timeout_s
            while self._status.state not in IDLE and time.monotonic() < end:
                time.sleep(0.05)
        self._q.put(("close", None))
        self._sup.join(5)
        self._worker_q.put(None)
        self._worker.join(5)
        if self._adb is not None:
            self._adb.stop()
        if self._lock.held:
            self._lock.release()

    # --- helpers ------------------------------------------------------------------------------------------------
    def _guard(self, epoch: int) -> None:
        st = self._status
        if st.state != S.READY or st.epoch != epoch:
            raise not_ready(f"epoch {epoch} is gone (now {st.state} {st.epoch})")

    def _missing_install(self) -> bool:
        p = self._paths
        if any(p.partial_marker(a).exists() for a in pins.ARCHIVES):   # setup stopped mid-extraction (3a review)
            return True
        return not (p.emulator_exe.exists() and p.adb_exe.exists() and p.avd_ini.exists()
                    and Path(f"{p.adbkey}.pub").exists())

    # --- status publishing (supervisor) -------------------------------------------------------------------------
    def _make_status(self) -> EngineStatus:
        return EngineStatus(state=self._state, text_key=self._text_key, progress=self._progress, mode=self._mode,
                            epoch=self._epoch, epoch_cause=self._cause, display_on=True, warnings=self._warnings,
                            error=self._error, caps=self._caps)

    def _set(self, state: EngineState, *, text_key: str | None = None, progress: float | None = None,
             error: OrcusError | None = None) -> None:
        self._state = state
        self._text_key = text_key or f"engine.state.{state}"
        self._progress = progress
        self._error = error
        self._publish()

    def _publish(self) -> None:
        st = self._make_status()
        self._write_owner(st=st)   # before anyone wakes up: nobody sees a state the record does not have yet
        with self._cond:
            self._status = st
            self._cond.notify_all()
        with self._subs_lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(st)
            except Exception:   # a subscriber must never break the supervisor
                _log.exception("status subscriber failed")
        _log.info("state %s epoch=%d cause=%s mode=%s %s", st.state, st.epoch, st.epoch_cause, st.mode,
                  f"{st.error.code}: {st.error.detail}" if st.error else "")

    def _write_owner(self, record: dict[str, Any] | None = None, st: EngineStatus | None = None) -> None:
        """engine_owner.json (§1.3): the owner and its ports from each launch, then the owner's own state whenever it
        changes. A read-only viewer (`orcus-cli engine status`) must not call the phone ready while SESSION_INIT
        still checks it; the guest's `booted` alone says so about 4 s early (3a)."""
        with self._owner_lock:
            if record is not None:
                self._owner = record
            if self._owner is None:
                return
            st = st or self._status
            now = {"state": str(st.state), "text_key": st.text_key, "epoch": st.epoch, "mode": str(st.mode)}
            if record is None and all(self._owner.get(k) == v for k, v in now.items()):
                return
            new = {**self._owner, **now}
            try:
                jsonio.write_json_atomic(self._paths.state_file("engine_owner.json"), new)
            except OSError as e:   # kept unwritten, so the next change tries again (3a review)
                _log.warning("owner record not written: %s", e)
                return
            self._owner = new

    # --- supervisor ---------------------------------------------------------------------------------------------
    def _supervise(self) -> None:
        while True:
            try:
                kind, payload = self._q.get(timeout=1.0)
            except queue.Empty:
                self._tick()
                continue
            if kind == "close":
                return
            try:
                self._handle(kind, payload)
            except Exception:
                _log.exception("supervisor failed on %s", kind)

    def _handle(self, kind: str, payload: Any) -> None:
        state = self._state
        match kind:
            case "start" if state in IDLE:
                self._begin(rebuild=False)
            case "retry":
                self._rebuilds_used = 0
                self._retried.clear()
                if state in IDLE:
                    self._begin(rebuild=False)
            case "rebuild":
                self._rebuilds_used = 0   # an explicit or confirmed rebuild resets the budget
                if state in IDLE:
                    self._begin(rebuild=True)
                elif state in ACTIVE or state == S.READY:
                    self._stopping(("rebuild", None))
            case "stop":
                if state in ACTIVE or state == S.READY:
                    self._stopping(("stop", None), fast=bool(payload))
                elif state == S.STOPPING:
                    self._intent = ("stop", None)   # e.g. stop during a restart's teardown
                elif state == S.FAILED:
                    self._release()
                    self._set(S.STOPPED)
            case "reset" if state == S.READY and self._vm is not None:
                if ErrorCode.ENGINE_NO_DISPLAY in self._warnings:   # the golden is fine: a restart loads it
                    self._stopping(("restart", EpochCause.RESET))
                elif self._mode == SessionMode.COLD:
                    self._stopping(("rebuild", None))   # COLD reset = rebuild (the GUI confirmed)
                else:
                    vm, nxt = self._vm, self._epoch + 1
                    self._set(S.RESETTING)
                    self._run(lambda c: self._job_reset(vm, nxt, c))
            case "enter":
                job_id, new_state, key, progress = payload
                if self._job is not None and self._job[0] == job_id and state in ACTIVE:
                    if key is None and new_state == state:   # a progress step keeps "rebuilding", not "first time"
                        key = self._text_key
                    self._set(new_state, text_key=key, progress=progress)
            case "done":
                job_id, outcome = payload
                if self._job is not None and self._job[0] == job_id:
                    self._job = None
                    self._on_outcome(outcome)
            case "vm_exit":
                vm, code = payload
                if vm is self._vm and state == S.READY:
                    self._on_crash(f"emulator exited with {code}")
            case "keepalive_lost":
                if payload is self._vm and state == S.READY:
                    self._on_crash("gRPC keepalive failed 3 times")
            case "adb_lost":
                vm, adb = payload
                cur = self._vm
                if cur is not None and vm is cur and adb is self._adb and state == S.READY:
                    _log.warning("private adb server ended; restarting it (§5.5)")
                    self._warnings = (*self._warnings, ErrorCode.ENGINE_ADB_LOST)
                    self._publish()
                    self._run(lambda c: self._job_adb_back(cur, c))
            case _:
                pass

    def _tick(self) -> None:
        if self._state != S.READY:
            return
        now = self._clock.monotonic()
        if now - self._last_disk < DISK_EVERY_S:
            return
        self._last_disk = now
        free = self._seams.disk_free(self._paths.root)
        if free < DISK_STOP:
            self._stopping(("fail", messages_ko.error(ErrorCode.PRE_DISK, f"{free / GIB:.1f} GiB free")), fast=True)
        elif free < DISK_WARN and ErrorCode.ENGINE_DISK_LOW not in self._warnings:
            self._warnings = (*self._warnings, ErrorCode.ENGINE_DISK_LOW)
            self._publish()

    def _run(self, job: Callable[[threading.Event], _Outcome]) -> None:
        self._job_seq += 1
        cancel = threading.Event()
        self._job = (self._job_seq, cancel)
        self._worker_q.put((self._job_seq, job, cancel))

    def _cancel_job(self) -> None:
        if self._job is not None:
            self._job[1].set()
            self._job = None   # its outcome is ignored; the job tears down what it launched

    def _begin(self, *, rebuild: bool) -> None:
        """Idle -> PREFLIGHT: take the lock first (§1.3), then the worker checks and boots."""
        self._warnings = ()
        if self._missing_install():
            self._set(S.FAILED, error=messages_ko.error(ErrorCode.PRE_NOT_INSTALLED))
            return
        if not self._lock.held:
            try:
                self._lock.acquire()
            except winjob.LockBusy:
                self._set(S.FAILED, error=messages_ko.error(ErrorCode.ENGINE_IN_USE))   # touch nothing
                return
        self._next_cause = EpochCause.REBUILD if rebuild else EpochCause.BOOT
        self._set(S.PREFLIGHT)
        self._run(lambda c: self._job_bring_up(c, force_rebuild=rebuild))

    def _stopping(self, intent: tuple[str, Any], *, fast: bool = False) -> None:
        self._cancel_job()
        self._intent = intent
        vm, self._vm = self._vm, None
        self._set(S.STOPPING)
        self._run(lambda c: self._job_teardown(vm, fast=fast))

    def _on_crash(self, detail: str) -> None:
        now = self._clock.monotonic()
        while self._crashes and now - self._crashes[0] > CRASH_WINDOW_S:
            self._crashes.popleft()
        self._crashes.append(now)
        _log.warning("crash: %s (%d in 5 min)", detail, len(self._crashes))
        if len(self._crashes) > 1:
            self._stopping(("fail", messages_ko.error(ErrorCode.ENGINE_CRASHED, detail)))
        else:
            self._stopping(("restart", EpochCause.CRASH_RESTART))

    def _on_outcome(self, out: _Outcome) -> None:
        match out:
            case _Ready():
                self._vm = out.vm
                self._epoch = out.epoch
                self._cause = out.cause
                self._caps = out.caps
                self._warnings = out.warnings
                if out.cause == EpochCause.RESET_RECOVERY:   # banner: the reset was not confirmed (§6.7)
                    self._warnings = (*self._warnings, ErrorCode.ENGINE_RESET_FAILED)
                self._mode = SessionMode.COLD if out.vm.mode == args.LaunchMode.COLD else SessionMode.GOLDEN
                self._watch(out.vm)
                self._set(S.READY)
            case _NeedProvision():
                self._mode = out.mode
                if out.rebuild:   # the first preparation reports BOOT, any later one REBUILD (§5.1)
                    self._next_cause = EpochCause.REBUILD
                self._set(S.PROVISIONING, text_key="engine.state.provisioning_rebuild" if out.rebuild else None)
                self._run(lambda c: self._job_provision(out.mode, out.rebuild, c))
            case _Provisioned():
                self._verify = out.record
                self._set(S.LAUNCHING)
                self._run(lambda c: self._job_bring_up(c, force_rebuild=False))
            case _GoldenBad():
                if self._rebuilds_used >= 1:   # one failure rebuild per run (§5.1)
                    self._fail(messages_ko.error(ErrorCode.ENGINE_GOLDEN_LOAD_FAILED, out.detail))
                else:
                    self._rebuilds_used += 1
                    self._verify = None
                    self._intent = ("rebuild", "gpu" if out.gpu_changed else None)
                    self._after_teardown()
            case _Retry():
                self._intent = ("restart", self._next_cause)
                self._after_teardown()
            case _ResetFailed():
                _log.warning("reset failed: %s", out.detail)
                self._stopping(("restart", EpochCause.RESET_RECOVERY))
            case _Fail():
                self._verify = None
                if self._vm is not None:   # e.g. a reset job that failed: stop the READY VM first (3a review)
                    self._stopping(("fail", out.error))
                else:
                    self._fail(out.error)
            case _Stopped():
                self._after_teardown()
            case _AdbBack():
                if out.vm is self._vm:
                    self._warnings = tuple(w for w in self._warnings if w != ErrorCode.ENGINE_ADB_LOST)
                    self._watch_adb(out.vm)
                    self._publish()
            case _AdbGone():
                self._stopping(("fail", out.error))   # the READY VM is ours to tear down
            case _:
                _log.warning("unknown outcome %s", out)

    def _after_teardown(self) -> None:
        intent, value = self._intent
        self._intent = ("stop", None)
        match intent:
            case "restart":
                self._next_cause = value or EpochCause.BOOT
                self._set(S.LAUNCHING)
                self._run(lambda c: self._job_bring_up(c, force_rebuild=False))
            case "rebuild":
                self._next_cause = EpochCause.REBUILD
                cold = self._settings().engine.gpu_mode == GpuMode.SOFTWARE
                self._mode = SessionMode.COLD if cold else SessionMode.GOLDEN
                self._set(S.PROVISIONING, text_key="engine.state.provisioning_gpu" if value == "gpu"
                          else "engine.state.provisioning_rebuild")
                mode = self._mode
                self._run(lambda c: self._job_provision(mode, True, c))
            case "fail":
                self._fail(value)
            case _:
                self._release()
                self._set(S.STOPPED)

    def _fail(self, error: OrcusError) -> None:
        self._release()
        self._set(S.FAILED, error=error)

    def _release(self) -> None:
        if self._lock.held:
            with self._owner_lock:
                self._owner = None
                try:   # a reader or a scanner may hold it; failing here froze the state machine (3a review)
                    self._paths.state_file("engine_owner.json").unlink(missing_ok=True)
                except OSError as e:
                    _log.warning("owner record not removed: %s", e)   # readers check the owner pid anyway
            self._lock.release()

    def _watch(self, vm: _Vm) -> None:
        """READY: the process and the gRPC keepalive (§5.7). Once per emulator."""
        if vm.watched:
            return
        vm.watched = True

        def procwait() -> None:
            while not vm.done.is_set():
                code = vm.proc.wait(0.5)
                if code is not None:
                    if not vm.done.is_set():
                        self._q.put(("vm_exit", (vm, code)))
                    return

        def keepalive() -> None:
            every = max(1.0, float(self._settings().engine.keepalive_s))
            fails = 0
            while not vm.done.wait(every):
                try:
                    vm.stub.getVmState(empty_pb2.Empty(), timeout=5)
                    fails = 0
                except grpc.RpcError as e:
                    if e.code() in (grpc.StatusCode.UNIMPLEMENTED, grpc.StatusCode.INVALID_ARGUMENT):
                        return
                    fails += 1
                    if fails >= KEEPALIVE_FAILS:
                        self._q.put(("keepalive_lost", vm))
                        return
        threading.Thread(target=procwait, name="engine-procwait", daemon=True).start()
        threading.Thread(target=keepalive, name="engine-keepalive", daemon=True).start()
        self._watch_adb(vm)

    def _watch_adb(self, vm: _Vm) -> None:
        """§4 adb-server-wait: our adb server ending while READY is reported; the supervisor decides."""
        adb = self._adb
        if adb is None:
            return

        def adbwait() -> None:
            while not vm.done.is_set():
                if adb.wait(0.5) is not None:
                    if not vm.done.is_set():
                        self._q.put(("adb_lost", (vm, adb)))
                    return
        threading.Thread(target=adbwait, name="engine-adbwait", daemon=True).start()

    def _job_adb_back(self, vm: _Vm, cancel: threading.Event) -> _Outcome:
        """Restart the private adb server (3 times per 5 minutes) and re-register the emulator (§5.5, S9)."""
        try:
            self._ensure_adb(vm.ports)
            self._adb_device(vm, cancel)
        except _Abort as a:
            return _AdbGone(a.outcome.error) if isinstance(a.outcome, _Fail) else a.outcome
        _log.info("private adb server back (pid %s)", self._adb.pid if self._adb else None)
        return _AdbBack(vm)

    # --- worker -------------------------------------------------------------------------------------------------
    def _work(self) -> None:
        while True:
            item = self._worker_q.get()
            if item is None:
                return
            job_id, job, cancel = item
            self._job_now = job_id
            try:
                outcome = job(cancel)
            except _Abort as a:
                outcome = a.outcome
            except provision.Cancelled:
                outcome = _Stopped()
            except Exception as e:   # a programming error ends as FAILED with the detail, never a hang
                _log.exception("engine job failed")
                outcome = _Fail(messages_ko.error(ErrorCode.ENGINE_UNAVAILABLE, f"{type(e).__name__}: {e}"))
            self._q.put(("done", (job_id, outcome)))

    def _enter(self, state: EngineState, key: str | None = None, progress: float | None = None) -> None:
        """key None: the state's own text, or for a progress step within the same state the text it shows now."""
        self._q.put(("enter", (self._job_now, state, key, progress)))

    @staticmethod
    def _check(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise provision.Cancelled()

    def _sleep(self, cancel: threading.Event, seconds: float) -> None:
        end = self._clock.monotonic() + seconds
        while (left := end - self._clock.monotonic()) > 0:
            self._check(cancel)
            self._clock.sleep(min(0.25, left))

    # bring-up: PREFLIGHT -> LAUNCHING -> CONNECTING -> BOOTING -> SESSION_INIT -> READY
    def _job_bring_up(self, cancel: threading.Event, *, force_rebuild: bool) -> _Outcome:
        settings = self._settings()
        mode = self._preflight(settings, force_rebuild)
        if isinstance(mode, _Outcome):
            return mode
        self._check(cancel)
        self._enter(S.LAUNCHING)
        vm = self._launch(settings, mode)
        try:
            self._enter(S.CONNECTING)
            self._connect(vm, cancel)
            self._enter(S.BOOTING)
            self._boot(vm, cancel, BOOT_GOLDEN_S if mode == args.LaunchMode.GOLDEN else BOOT_COLD_S)
            self._enter(S.SESSION_INIT)
            ready = self._session_init(vm, settings, cancel, cause=self._next_cause, epoch=self._epoch + 1)
            self._check(cancel)   # a stop or rebuild meanwhile drops this outcome: the VM must not be left running
            return ready
        except BaseException:
            self._teardown(vm, fast=cancel.is_set())
            raise

    def _preflight(self, settings: Settings, force_rebuild: bool) -> "args.LaunchMode | _Outcome":
        problems = self._seams.home_problems(self._paths.root)
        if problems:
            raise _fail(ErrorCode.PRE_PATH, "; ".join(problems))
        if not self._seams.whpx_on():
            raise _fail(ErrorCode.PRE_WHPX_OFF)
        self._cleanup_orphans()
        cold = settings.engine.gpu_mode == GpuMode.SOFTWARE
        mode = SessionMode.COLD if cold else SessionMode.GOLDEN
        if self._verify is not None and self._verify.mode == mode:
            record: snapshots.Record | None = self._verify     # the verification boot after provisioning
        elif force_rebuild:
            record = None
        else:
            record = snapshots.usable(self._paths, snapshots.current_key(self._paths, settings), mode)
        free = self._seams.disk_free(self._paths.root)
        if free < (DISK_PROVISION_MIN if record is None else DISK_RUN_MIN):
            raise _fail(ErrorCode.PRE_DISK, f"{free / GIB:.1f} GiB free")
        if record is None:
            rebuild = force_rebuild or snapshots.read(self._paths) is not None or snapshots.golden_exists(self._paths)
            return _NeedProvision(mode, rebuild)
        self._provision_id = record.provision_id
        if not cold and self._seams.displays() == 0 and snapshots.golden_gpu(self._paths) != "":
            # The emulator takes the host GPU from this session's display devices, and there are none in a
            # disconnected Remote Desktop session: the load would fail as 'host GPU has changed' and the emulator
            # would delete a good golden (3a finding 18). Boot COLD this once and keep the golden.
            if record is self._verify:   # the verification boot must load the new golden (§6.5 step 10)
                raise _fail(ErrorCode.PRE_NO_DISPLAY, "no display device for the verification boot")
            _log.warning("no display device: COLD boot this once instead of loading the golden")
            return args.LaunchMode.COLD
        return args.LaunchMode.COLD if cold else args.LaunchMode.GOLDEN

    def _cleanup_orphans(self) -> None:
        """With the lock held: a live VM of our AVD is an orphan; dead discovery files go (§5.7). A record's pid is
        ended only while it is a qemu under our SDK (and not us): PIDs are reused, and a stale record survives a
        power cut or a killed app (3a review)."""
        ours = {pid for pid, image in self._seams.procs.sdk_processes() if "qemu-system" in image.lower()}
        for rec in self._seams.discovery.ours():
            if rec.pid in ours and rec.pid != os.getpid():
                _log.warning("terminating orphan VM pid %d", rec.pid)
                self._seams.procs.terminate(rec.pid)
            try:
                rec.source.unlink(missing_ok=True)
            except OSError:
                pass
        if any("qemu-system" in image.lower() for _, image in self._seams.procs.sdk_processes()):
            return
        for lock in self._paths.avd_data.glob("*.lock"):
            try:
                if lock.is_dir():
                    for f in lock.iterdir():
                        f.unlink(missing_ok=True)
                    lock.rmdir()
                else:
                    lock.unlink(missing_ok=True)
            except OSError:
                pass

    def _ensure_adb(self, ports: Ports) -> AdbServer:
        if self._adb is None or self._adb.port != ports.adb_server:
            if self._adb is not None:
                self._adb.stop()
            self._adb = AdbServer(self._paths, self._seams, ports.adb_server)
        if self._adb.recent_restarts() >= 3:
            raise _fail(ErrorCode.ENGINE_ADB_LOST, "adb server restarted 3 times in 5 min")
        try:
            self._adb.ensure()
        except OSError as e:   # includes TimeoutError
            raise _fail(ErrorCode.PRE_PORTS, f"private adb server: {e}") from e
        return self._adb

    def _launch(self, settings: Settings, mode: args.LaunchMode) -> _Vm:
        ports = self._seams.ports.choose(settings.engine.ports)
        self._seams.ports.save(ports)
        adb = self._ensure_adb(ports)
        proxy = childenv.decide_proxy(os.environ)
        cfg = args.LaunchConfig(self._paths.emulator_exe, ports.console, ports.grpc, settings.engine.idle_grpc_timeout_s,
                                settings.engine.timezone, settings.engine.locale, proxy.http_proxy_flag)
        gpu_mode = GpuMode.SOFTWARE if mode == args.LaunchMode.COLD and settings.engine.gpu_mode == GpuMode.SOFTWARE \
            else settings.engine.gpu_mode
        argv = args.build(cfg, mode, gpu_mode)
        log_path = self._paths.logs / f"emulator-{time.strftime('%Y%m%d-%H%M%S')}-{mode}.log"
        proc = self._seams.procs.spawn(argv, env=self._seams.env(ports.adb_server), log_path=log_path)
        _log.info("launched %s: %s", mode, " ".join(argv[1:]))
        self._write_owner({"pid": os.getpid(), "started": time.time(), "kind": self._owner_kind,
                           "console": ports.console, "grpc": ports.grpc, "adb_server": ports.adb_server,
                           "adb_server_pid": adb.pid})
        return _Vm(mode, ports, proc, log_path)

    def _exited(self, vm: _Vm) -> _Abort:
        """The emulator ended on its own while starting: classify its log tail (§5.1 table)."""
        try:
            text = vm.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        golden = vm.mode == args.LaunchMode.GOLDEN
        c = exitlog.classify(exitlog.tail(text, 40), golden=golden)
        excerpt = exitlog.tail(text)
        if c.golden_load_failed or (golden and c.code == ErrorCode.ENGINE_EXIT
                                    and not snapshots.golden_exists(self._paths)):
            return _Abort(_GoldenBad("golden did not load: " + excerpt[-300:], gpu_changed=c.gpu_changed))
        assert c.code is not None
        if c.retry and str(c.code) not in self._retried:
            self._retried.add(str(c.code))
            return _Abort(_Retry(str(c.code)))
        return _fail(c.code, f"emulator exited with {vm.proc.poll()}", excerpt)

    def _connect(self, vm: _Vm, cancel: threading.Event) -> None:
        limit = CONNECT_FIRST_S if self._first_connect else CONNECT_S
        end = self._clock.monotonic() + limit
        while vm.record is None:
            self._check(cancel)
            if not vm.alive():
                raise self._exited(vm)
            if self._clock.monotonic() > end:
                raise _fail(ErrorCode.ENGINE_CONNECT_TIMEOUT, f"no discovery file in {limit:.0f} s")
            rec = self._seams.discovery.find(vm.ports.console)
            if rec is not None and rec.grpc_port and rec.grpc_token:
                vm.record = rec
                vm.channel = self._seams.channel(rec.grpc_port, rec.grpc_token)
                vm.stub = grpc_channel.controller(vm.channel)
                vm.snap = grpc_channel.snapshots(vm.channel)
            else:
                self._clock.sleep(0.25)
        while True:
            self._check(cancel)
            try:
                vm.stub.getStatus(empty_pb2.Empty(), timeout=5)
                break
            except grpc.RpcError:
                if not vm.alive():
                    raise self._exited(vm) from None
                if self._clock.monotonic() > end:
                    raise _fail(ErrorCode.ENGINE_CONNECT_TIMEOUT, "getStatus never answered") from None
                self._clock.sleep(0.5)
        self._first_connect = False
        assert self._adb is not None
        vm.shell = RawShell(self._seams.adb, self._adb.port, vm.ports.serial)

    def _boot(self, vm: _Vm, cancel: threading.Event, limit_s: float) -> None:
        end = self._clock.monotonic() + limit_s
        assert vm.shell is not None
        booted = False
        while self._clock.monotonic() < end:
            self._check(cancel)
            if not vm.alive():
                raise self._exited(vm)
            try:
                booted = booted or vm.stub.getStatus(empty_pb2.Empty(), timeout=5).booted
            except grpc.RpcError:
                pass
            if booted and vm.shell.state() == "device" and vm.shell.text("getprop sys.boot_completed", 10) == "1":
                vm.booted_at = self._clock.monotonic()
                return
            self._clock.sleep(0.5)
        if vm.mode == args.LaunchMode.GOLDEN:   # S4: a broken golden hangs without exiting or being deleted
            raise _Abort(_GoldenBad(f"GOLDEN boot did not finish in {limit_s:.0f} s"))
        raise _fail(ErrorCode.ENGINE_BOOT_TIMEOUT, f"not booted in {limit_s:.0f} s")

    def _adb_device(self, vm: _Vm, cancel: threading.Event) -> None:
        assert vm.shell is not None and self._adb is not None
        end = self._clock.monotonic() + ADB_DEVICE_S
        registered = False
        while self._clock.monotonic() < end:
            self._check(cancel)
            if vm.shell.state() == "device":
                return
            if not registered:   # §5.5: the registration message brings it back at once (S9)
                self._adb.register(vm.ports.adb)
                registered = True
            self._clock.sleep(0.5)
        raise _fail(ErrorCode.ENGINE_ADB_LOST, "adb never reported the device")

    def _session_init(self, vm: _Vm, settings: Settings, cancel: threading.Event, *, cause: EpochCause,
                      epoch: int) -> _Outcome:
        """§5.1 SESSION_INIT checks, §6.6 session_init, then a new DeviceSession for the new epoch."""
        assert vm.shell is not None
        sh = vm.shell
        self._adb_device(vm, cancel)
        if vm.mode == args.LaunchMode.GOLDEN:
            marker = sh.text("getprop debug.orcus.golden", 10)
            if marker != self._provision_id:
                raise _Abort(_GoldenBad(f"golden marker {marker!r}, expected {self._provision_id!r}"))
            problem = provision.framework_problem(sh)   # a golden that comes back without its framework
            if problem is not None:
                raise _Abort(_GoldenBad(f"after the load: {problem}"))
        try:
            log_text = vm.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log_text = ""
        hypervisor = accelerator_from_log(log_text) or "UNKNOWN"
        if hypervisor != "WHPX":   # getStatus.vmConfig.hypervisorType is always UNKNOWN in 37.2.12 (S0b)
            raise _fail(ErrorCode.PRE_WHPX_OFF, f"accelerator {hypervisor}")
        renderer = gpu.renderer(sh.text(gpu.RENDERER_CMD, 15))
        warnings = [ErrorCode.ENGINE_GPU_SOFTWARE] if (gpu.is_software(renderer)
                                                      or settings.engine.gpu_mode == GpuMode.SOFTWARE) else []
        if vm.mode == args.LaunchMode.COLD and settings.engine.gpu_mode != GpuMode.SOFTWARE:
            warnings.append(ErrorCode.ENGINE_NO_DISPLAY)   # the COLD boot that kept the golden (_preflight)
        self._frame_check(vm, cancel)
        vm.nonce = uuid.uuid4().hex[:12]
        presets = settings.sensors.location_presets
        lat, lon = (presets[0][0], presets[0][1]) if presets else DEFAULT_LOCATION
        init = provision.session_init(sh, nonce=vm.nonce, lat=lat, lon=lon,
                                      rerun_ram_props=vm.mode == args.LaunchMode.COLD)
        if init.failures:
            _log.warning("session_init: %s", init.failures)
        try:
            vm.stub.setMicrophoneState(ec.MicrophoneState(realAudioEnabled=False), timeout=5)
        except grpc.RpcError as e:
            _log.warning("setMicrophoneState: %s", e.code())
        caps = self._caps_for(vm, renderer, settings.engine.gpu_mode, hypervisor)
        if self._verify is not None:   # verification boot right after provisioning (§6.5 step 10)
            if vm.mode == args.LaunchMode.GOLDEN:
                self._sleep(cancel, POST_LOAD_HEALTH_S - (self._clock.monotonic() - vm.booted_at))
                if sh.text("echo ok", 10) != "ok":
                    raise _Abort(_GoldenBad(f"adb stopped answering {POST_LOAD_HEALTH_S:.0f} s after the load"))
                problem = provision.framework_problem(sh)
                if problem is not None:
                    raise _Abort(_GoldenBad(f"{POST_LOAD_HEALTH_S:.0f} s after the load: {problem}"))
            snapshots.write(self._paths, self._verify)
            self._verify = None
        if self._factory is not None:
            assert vm.record is not None and vm.record.grpc_token and vm.record.grpc_port
            vm.session = self._factory(DeviceEndpoint(f"localhost:{vm.record.grpc_port}", vm.record.grpc_token,
                                                      vm.ports.serial, epoch))
        return _Ready(vm, caps, tuple(warnings), epoch, cause)

    def _frame_check(self, vm: _Vm, cancel: threading.Event) -> None:
        end = self._clock.monotonic() + FRAME_CHECK_S
        while self._clock.monotonic() < end:
            self._check(cancel)
            try:
                img = vm.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888), timeout=5)
                w, h = img.format.width, img.format.height   # 37.2.12 leaves Image.width/height at 0 (S2)
                if w and h and len(img.image) == w * h * 3:
                    return
            except grpc.RpcError:
                pass
            self._clock.sleep(0.5)
        raise _fail(ErrorCode.ENGINE_NO_FRAMES)

    def _caps_for(self, vm: _Vm, renderer: str, gpu_mode: GpuMode, hypervisor: str) -> DeviceCaps:
        """DeviceCaps, cached in state\\device_caps.json per golden and renderer."""
        path = self._paths.state_file("device_caps.json")
        cached = jsonio.read_json(path)
        if isinstance(cached, dict) and cached.get("provision_id") == self._provision_id \
                and cached.get("renderer") == renderer and isinstance(cached.get("caps"), dict):
            c = cached["caps"]
            try:
                return DeviceCaps(api=int(c["api"]), abis=tuple(c["abis"]), panel=tuple(c["panel"]),
                                  renderer=renderer, gpu_mode=gpu_mode, hypervisor=hypervisor, locale=c["locale"],
                                  timezone=c["timezone"], tts_engine=c["tts_engine"], tts_ko=c["tts_ko"],
                                  speech_recognizer=bool(c["speech_recognizer"]), imes=tuple(c["imes"]))
            except (KeyError, TypeError, ValueError):
                pass
        status = vm.stub.getStatus(empty_pb2.Empty(), timeout=5)
        hw = {e.key: e.value for e in status.hardwareConfig.entry}
        panel = (int(hw.get("hw.lcd.width", "720")), int(hw.get("hw.lcd.height", "1280")),
                 int(hw.get("hw.lcd.density", "320")))
        assert vm.shell is not None
        caps = provision.detect_caps(vm.shell, panel=panel, renderer=renderer, gpu_mode=gpu_mode, hypervisor=hypervisor)
        jsonio.write_json_atomic(path, {"provision_id": self._provision_id, "renderer": renderer, "caps": {
            "api": caps.api, "abis": list(caps.abis), "panel": list(caps.panel), "locale": caps.locale,
            "timezone": caps.timezone, "tts_engine": caps.tts_engine, "tts_ko": caps.tts_ko,
            "speech_recognizer": caps.speech_recognizer, "imes": list(caps.imes)}})
        return caps

    # provisioning (§6.5)
    def _job_provision(self, mode: SessionMode, rebuild: bool, cancel: threading.Event) -> _Outcome:
        settings = self._settings()
        if mode == SessionMode.GOLDEN and self._seams.displays() == 0:
            # the golden would record no host GPU, and the first load with a display would delete it (finding 18)
            raise _fail(ErrorCode.PRE_NO_DISPLAY, "no display device to record in the golden")
        start = self._clock.monotonic()
        provision_id = uuid.uuid4().hex[:12]
        key = snapshots.current_key(self._paths, settings)
        step = ["launch"]

        def failed(detail: str) -> _Abort:
            return _fail(ErrorCode.ENGINE_PROVISION_FAILED, f"{step[0]}: {detail}")

        vm = self._launch(settings, args.LaunchMode.REBUILD if rebuild else args.LaunchMode.PROVISION)
        try:
            self._connect(vm, cancel)
            step[0] = "boot"
            self._boot(vm, cancel, min(BOOT_COLD_S, PROVISION_LIMIT_S - (self._clock.monotonic() - start)))
            assert vm.shell is not None
            step[0] = "framework restart"
            waited = provision.wait_framework_restart(vm.shell, self._clock, cancel)
            _log.info("framework ready %.1f s after boot", waited)
            step[0] = "renderer"
            renderer = gpu.renderer(vm.shell.text(gpu.RENDERER_CMD, 15))
            if mode == SessionMode.GOLDEN and gpu.is_software(renderer):
                raise failed(f"software renderer {renderer!r} in a host GPU mode")
            step[0] = "settings"
            self._enter(S.PROVISIONING, None, 0.4)
            applied = provision.apply_checked(vm.shell, self._clock, provision.PROVISION, cancel)
            bad = {k: v for k, v in applied.items() if v != "ok"}
            if bad:
                raise failed(str(bad))
            provision.flush(vm.shell, self._clock)
            observed = {"locale": vm.shell.text("getprop persist.sys.locale", 10),
                        "timezone": vm.shell.text("getprop persist.sys.timezone", 10), "renderer": renderer}
            if observed["locale"] != settings.engine.locale or observed["timezone"] != settings.engine.timezone:
                raise failed(f"observed {observed}")
        finally:
            self._teardown(vm, fast=cancel.is_set())
        if mode == SessionMode.COLD:
            record = snapshots.Record(mode, provision_id, key, observed)
            snapshots.write(self._paths, record)
            return _Provisioned(record)
        # 7a: never save in the provisioning session; a clean cold boot saves (S4)
        step[0] = "clean boot"
        if self._seams.displays() == 0:   # this boot's host GPU goes into the golden (finding 18)
            raise _fail(ErrorCode.PRE_NO_DISPLAY, "display device gone before the clean boot")
        self._enter(S.PROVISIONING, "engine.state.provisioning_save", 0.7)
        vm = self._launch(settings, args.LaunchMode.COLD)
        try:
            self._connect(vm, cancel)
            self._boot(vm, cancel, BOOT_COLD_S)
            assert vm.shell is not None
            self._sleep(cancel, SETTLE_BEFORE_SAVE_S)
            step[0] = "persisted settings"
            gone = provision.missing(vm.shell)
            if gone:
                raise failed(f"not kept after the reboot: {gone}")
            step[0] = "markers"
            ram = provision.apply_checked(vm.shell, self._clock, provision.RAM_PROPS, cancel)
            if any(v != "ok" for v in ram.values()) or not provision.set_markers(vm.shell, provision_id):
                raise failed(str(ram))
            step[0] = "quiet"   # first-boot compilation still running: wait for the guest to settle
            quiet_start = self._clock.monotonic()
            calm = 0
            busy = provision.cpu_busy(vm.shell)
            while self._clock.monotonic() - quiet_start < QUIET_WAIT_S:
                self._check(cancel)
                calm = calm + 1 if busy is not None and busy < QUIET_BUSY else 0
                if calm >= 2 or busy is None:
                    break
                self._clock.sleep(1.0)
                busy = provision.cpu_busy(vm.shell)
            _log.info("guest %s before the save after %.0f s (cpu busy %s, load average %s)",
                      "quiet" if calm >= 2 else "still busy", self._clock.monotonic() - quiet_start,
                      None if busy is None else round(busy, 2), provision.load_average(vm.shell))
            problem = provision.framework_problem(vm.shell)
            if problem is not None:
                raise failed(problem)
            step[0] = "save"
            snapshots.clear(self._paths)   # never point at a golden that is being replaced
            vm.snap.DeleteSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN_SNAPSHOT), timeout=60)
            saved = vm.snap.SaveSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN_SNAPSHOT), timeout=300)
            if not saved.success:
                raise failed(f"SaveSnapshot: {saved.err.decode(errors='replace')}")
            listed = vm.snap.ListSnapshots(ss.SnapshotFilter(statusFilter=ss.SnapshotFilter.All), timeout=30)
            if not any(s.snapshot_id == GOLDEN_SNAPSHOT for s in listed.snapshots):
                raise failed("golden missing from ListSnapshots after the save")
        finally:
            self._teardown(vm, fast=cancel.is_set())
        # the verification boot checks the markers and only then writes provision.json (§6.5 step 10)
        return _Provisioned(snapshots.Record(mode, provision_id, snapshots.golden_key(key), observed))

    # reset (§5.1 RESETTING, §6.7)
    def _job_reset(self, vm: _Vm, epoch: int, cancel: threading.Event) -> _Outcome:
        if vm.session is not None:
            try:
                vm.session.input.release_all()
            except Exception:
                _log.exception("release_all before reset")
        old_nonce = vm.nonce
        try:
            loaded = vm.snap.LoadSnapshot(ss.SnapshotPackage(snapshot_id=GOLDEN_SNAPSHOT), timeout=60)
        except grpc.RpcError as e:
            return _ResetFailed(f"LoadSnapshot: {e.code()}")
        if not loaded.success:
            return _ResetFailed(f"LoadSnapshot: {loaded.err.decode(errors='replace')}")
        try:
            self._adb_device(vm, cancel)
        except _Abort:
            return _ResetFailed("adb did not come back after the load")
        assert vm.shell is not None
        nonce_now = vm.shell.text("getprop debug.orcus.session", 10)
        marker = vm.shell.text("getprop debug.orcus.golden", 10)
        if nonce_now == old_nonce or marker != self._provision_id:   # §6.7: 'different', not 'empty'
            return _ResetFailed(f"not verified (session {nonce_now!r}, golden {marker!r})")
        if vm.session is not None:
            vm.session.close()
            vm.session = None
        vm.booted_at = self._clock.monotonic()
        try:
            return self._session_init(vm, self._settings(), cancel, cause=EpochCause.RESET, epoch=epoch)
        except _Abort as a:
            detail = a.outcome.error.detail if isinstance(a.outcome, _Fail) else type(a.outcome).__name__
            return _ResetFailed(detail)

    # teardown (§5.1 STOPPING)
    def _job_teardown(self, vm: _Vm | None, *, fast: bool) -> _Outcome:
        if vm is not None:
            self._teardown(vm, fast=fast)
        return _Stopped()

    def _teardown(self, vm: _Vm, *, fast: bool) -> None:
        vm.done.set()
        if vm.session is not None:
            try:
                vm.session.close()
            except Exception:
                _log.exception("DeviceSession.close")
            vm.session = None
        if vm.alive() and vm.stub is not None:
            try:   # QEMU drains and flushes the disks when it pauses; nothing is saved at exit (-no-snapshot-save)
                vm.stub.setVmState(ec.VmRunState(state=ec.VmRunState.PAUSED), timeout=PAUSE_FAST_S if fast else PAUSE_S)
            except grpc.RpcError as e:
                _log.info("PAUSED: %s", e.code())
        # No graceful SHUTDOWN: qemu's exit path can fail fast in combase.dll (0xc0000602), and Windows then holds the
        # process for seconds and files a crash report under C:\ProgramData (3a: 31 in two days, §11.5).
        if vm.alive():
            if vm.record is not None:   # qemu first: ending only emulator.exe leaves qemu running (P)
                self._seams.procs.terminate(vm.record.pid)
            vm.proc.kill()
            vm.proc.wait(5)
        if vm.record is not None:       # the emulator removes its discovery file only when it exits by itself
            try:
                vm.record.source.unlink(missing_ok=True)
            except OSError as e:
                _log.info("discovery file left: %s", e)
        if vm.channel is not None:
            vm.channel.close()
        keep = self._adb.pid if self._adb is not None else None
        # S6: what stays is the `emulator -kill … -sleep 20` watchdog (it would linger 20 s); safe to end now.
        # Only the emulator's own processes: never an adb server (a teacher's 5037 one) or emulator-check (3a review)

        def leftovers() -> list[tuple[int, str]]:
            return [(pid, image) for pid, image in self._seams.procs.sdk_processes()
                    if pid != keep and Path(image).name.lower() in EMULATOR_IMAGES]
        end = self._clock.monotonic() + 0.5
        left = leftovers()
        while left and self._clock.monotonic() < end:
            self._clock.sleep(0.25)
            left = leftovers()
        for pid, image in left:
            _log.info("ending leftover %s (%d)", Path(image).name, pid)
            self._seams.procs.terminate(pid)
