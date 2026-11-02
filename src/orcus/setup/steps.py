"""Setup step model (ARCHITECTURE.md §11.6), the SetupApi implementation.

CHECK -> LICENSE -> WHPX -> DOWNLOAD -> EXTRACT -> REBOOT -> ACCEL -> AVD -> PROVISION -> DONE.
Every step is idempotent and state\\setup.json remembers what happened, so a second run changes
nothing (A1). Steps that need a human (license, UAC) stop in "waiting_user" until the matching
method is called. The PROVISION step stays pending here: the engine prepares the phone the first time the window
starts it (EngineStatus PROVISIONING), so setup never builds an engine (§11.6 step 9).
"""

import threading
import time
import traceback
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from orcus.common import accel, acl, hostinfo, jsonio, log, messages_ko, pins, win32
from orcus.common.avd import USERDATA_GIB, write_avd
from orcus.common.paths import OrcusPaths, home_problems, interpreter_problems
from orcus.contracts import (
    SETUP_FREE_GIB,
    CmdResult,
    ErrorCode,
    LicenseText,
    OrcusError,
    OrcusFailure,
    Settings,
    SetupEvent,
    SetupStep,
)

from . import download as dl
from . import extract, license, repo_index, whpx

GIB = 1 << 30
ORDER = (SetupStep.CHECK, SetupStep.LICENSE, SetupStep.WHPX, SetupStep.DOWNLOAD, SetupStep.EXTRACT,
         SetupStep.REBOOT, SetupStep.ACCEL, SetupStep.AVD, SetupStep.PROVISION, SetupStep.DONE)
_FALLBACK_CODE = {
    SetupStep.CHECK: ErrorCode.SETUP_PATH,
    SetupStep.LICENSE: ErrorCode.SETUP_NETWORK,
    SetupStep.WHPX: ErrorCode.SETUP_DISM_FAILED,
    SetupStep.DOWNLOAD: ErrorCode.SETUP_NETWORK,
    SetupStep.EXTRACT: ErrorCode.SETUP_QUARANTINE,
    SetupStep.REBOOT: ErrorCode.SETUP_REBOOT_PENDING,
    SetupStep.ACCEL: ErrorCode.SETUP_ACCEL_FAILED,
    SetupStep.AVD: ErrorCode.SETUP_DISK,
}
_log = log.get("setup")


@dataclass
class SetupDeps:
    """Seams. app.compose wires the real ones; tests pass fakes."""

    paths: OrcusPaths
    settings: Settings
    fetch_repo_index: Callable[[], repo_index.RepoIndex]
    fetch_sysimg_index: Callable[[], repo_index.RepoIndex]
    query_whpx: Callable[[], accel.WhpxState | None]
    reboot_pending: Callable[[], bool]
    boot_time: Callable[[], float]
    run_dism: Callable[[], whpx.DismOutcome]
    download: Callable[..., Path]
    install_archive: Callable[..., extract.Verification]
    verify_installed: Callable[[OrcusPaths, pins.Archive], extract.Verification]
    emulator_check: Callable[[], accel.AccelReport]
    adb_keygen: Callable[[], CmdResult]
    ensure_job: Callable[[], None]
    disk_free: Callable[[], int]
    register_resume: Callable[[], None]
    unregister_resume: Callable[[], None]
    check_interpreter: bool = True
    check_home: Callable[[Path], list[str]] = home_problems
    archives: Sequence[pins.Archive] = field(default=pins.ARCHIVES)
    boot_id: Callable[[], int | None] = win32.boot_id   # None: compare boot times instead
    lock_root: Callable[[Path, str], CmdResult] = acl.lock_to_user   # the root becomes this account's only
    verify_zip: Callable[[Path, pins.Archive], bool] = lambda zip_path, a: dl.verified(zip_path, a.size, a.sha1)


@dataclass(frozen=True)
class _Done:
    pass


@dataclass(frozen=True)
class _Failed:
    error: OrcusError
    can_continue: bool = False


@dataclass(frozen=True)
class _Stop:   # the run ends here waiting for something outside (a reboot)
    pass


type _Outcome = _Done | _Failed | _Stop


class SetupService:
    def __init__(self, deps: SetupDeps) -> None:
        self._d = deps
        self._paths = deps.paths
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._decisions: dict[str, Any] = {}
        self._subs: list[Callable[[SetupEvent], None]] = []
        self._events: dict[SetupStep, SetupEvent] = {s: self._event(s, "pending") for s in ORDER}
        self._paused = threading.Event()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None   # None once the worker has ended
        self._restart = False                            # run() came while a cancelled worker was ending
        self._pending_licenses: list[LicenseText] = []
        self._license_texts: list[LicenseText] = []
        self._resolved: list[repo_index.Resolved] | None = None
        self._state: dict[str, Any] = self._load_state()

    # SetupApi --------------------------------------------------------------------------------
    def events(self) -> tuple[SetupEvent, ...]:
        with self._lock:
            return tuple(self._events[s] for s in ORDER)

    def run(self) -> None:
        """Start from the first step; done steps pass at once. A cancelled worker that is still ending starts the
        new run itself when it is gone, so the caller never waits for it."""
        with self._lock:
            if self._thread is not None:
                if self._cancel.is_set():
                    self._restart = True
                return
            self._start()

    def license(self) -> LicenseText | None:
        with self._lock:
            waiting = self._events[SetupStep.LICENSE].state == "waiting_user"
            return self._pending_licenses[0] if waiting and self._pending_licenses else None

    def accept_license(self, sha256: str, accepted_by: str) -> None:
        self._decide("license", (sha256, accepted_by))

    def enable_whpx(self) -> None:
        self._decide("whpx", True)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        """Resume a paused download, or continue past a WHPX failure ([Download anyway])."""
        self._paused.clear()
        self._decide("continue", True)

    def subscribe(self, cb: Callable[[SetupEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(cb)

        def unsubscribe() -> None:
            with self._lock:
                if cb in self._subs:
                    self._subs.remove(cb)
        return unsubscribe

    def cancel(self) -> None:
        with self._lock:
            self._restart = False
        self._cancel.set()
        with self._cond:
            self._cond.notify_all()

    # extras for the CLI and tests --------------------------------------------------------------
    def join(self, timeout: float | None = None) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
            return not t.is_alive()
        return True

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    # plumbing ----------------------------------------------------------------------------------
    @staticmethod
    def _event(step: SetupStep, state: str, *, progress: float | None = None, done: int = 0, total: int = 0,
               rate: float = 0.0, eta: float | None = None, problem: OrcusError | None = None,
               can_continue: bool = False) -> SetupEvent:
        return SetupEvent(step, state, progress, done, total, rate, eta, problem, can_continue)

    def _emit(self, step: SetupStep, state: str, **kw: Any) -> None:
        event = self._event(step, state, **kw)
        with self._lock:
            self._events[step] = event
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(event)
            except Exception:   # a broken subscriber must not stop setup
                _log.exception("setup subscriber failed")

    def _decide(self, kind: str, value: Any) -> None:
        with self._cond:
            self._decisions[kind] = value
            self._cond.notify_all()

    def _wait_decision(self, kind: str) -> Any | None:
        with self._cond:
            while kind not in self._decisions:
                if self._cancel.is_set():
                    return None
                self._cond.wait(0.5)
            return self._decisions.pop(kind)

    def _load_state(self) -> dict[str, Any]:
        data = jsonio.read_json(self._paths.state_file("setup.json"))
        return data if isinstance(data, dict) else {"version": 1}

    def _save_state(self) -> None:
        """Never fatal: with D: full the worker must still report the failing step (the next run redoes it)."""
        self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            jsonio.write_json_atomic(self._paths.state_file("setup.json"), self._state)
        except OSError as e:
            _log.error("setup.json not saved: %s", e)

    def _mark(self, step: SetupStep, status: str) -> None:
        self._state.setdefault("steps", {})[str(step)] = status
        self._save_state()

    def _start(self) -> None:
        """A fresh run, under self._lock: no pause, cancel or decision of an earlier run carries over."""
        self._cancel.clear()
        self._paused.clear()
        self._restart = False
        with self._cond:
            self._decisions.clear()
        self._thread = threading.Thread(target=self._worker, name="setup-worker", daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        try:
            self._steps()
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    if self._restart:
                        self._start()

    def _steps(self) -> None:
        handlers: dict[SetupStep, Callable[[], _Outcome]] = {
            SetupStep.CHECK: self._check, SetupStep.LICENSE: self._license, SetupStep.WHPX: self._whpx,
            SetupStep.DOWNLOAD: self._download, SetupStep.EXTRACT: self._extract, SetupStep.REBOOT: self._reboot,
            SetupStep.ACCEL: self._accel, SetupStep.AVD: self._avd,
        }
        for step in ORDER:
            if self._cancel.is_set():
                return
            if step is SetupStep.PROVISION:
                _log.info("provision: the engine prepares the phone when the window first starts it")
                continue
            if step is SetupStep.DONE:
                self._state["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                self._mark(step, "done")
                self._emit(step, "done", progress=1.0)
                return
            self._emit(step, "running")
            try:
                outcome = handlers[step]()
            except OrcusFailure as f:
                outcome = _Failed(f.error)
            except (dl.Cancelled, extract.Cancelled):
                return
            except Exception as e:   # unexpected: keep the trace in setup.log, show a short line
                _log.error("step %s crashed:\n%s", step, traceback.format_exc())
                outcome = _Failed(messages_ko.error(_FALLBACK_CODE[step], f"{type(e).__name__}: {e}",
                                                    message_key="setup.unexpected"))
            match outcome:
                case _Done():
                    self._mark(step, "done")
                    self._emit(step, "done", progress=1.0)
                case _Stop():
                    self._mark(step, "waiting_user")
                    return
                case _Failed(error=err, can_continue=can_continue):
                    _log.warning("step %s failed: %s %s", step, err.code, err.detail)
                    with self._cond:
                        self._decisions.pop("continue", None)   # only a decision made after this counts
                    self._mark(step, "failed")
                    self._emit(step, "failed", problem=err, can_continue=can_continue)
                    if not can_continue or self._wait_decision("continue") is None:
                        return

    # steps -------------------------------------------------------------------------------------
    def _missing_archives(self) -> list[pins.Archive]:
        return [a for a in self._d.archives if not self._d.verify_installed(self._paths, a).ok]

    def _check(self) -> _Outcome:
        reasons = self._d.check_home(self._paths.root)
        if self._d.check_interpreter:
            reasons += interpreter_problems()
        if reasons:
            detail = " ".join(messages_ko.text(r) for r in reasons)
            return _Failed(messages_ko.error(ErrorCode.SETUP_PATH, f"{self._paths.root}: {detail}"))
        self._paths.ensure()
        sid = win32.user_sid()
        if sid and self._state.get("root_locked_to") != sid:   # once per account (common/acl.py)
            locked = self._d.lock_root(self._paths.root, sid)
            if locked.code != 0:
                detail = (locked.err or locked.out).strip()[-300:]
                return _Failed(messages_ko.error(ErrorCode.SETUP_PERMISSIONS, f"{self._paths.root}: {detail}"))
            self._state["root_locked_to"] = sid
            _log.info("%s is now this account's only (SYSTEM and Administrators keep access)", self._paths.root)
        missing = self._missing_archives()
        free = self._d.disk_free()
        av = hostinfo.antivirus()
        state = self._d.query_whpx()
        self._state["check"] = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "whpx_install_state": int(state) if state is not None else None,
            "reboot_pending_key": self._d.reboot_pending(),
            "elevation": hostinfo.elevation(),
            "virtualization_firmware_flag": win32.virtualization_firmware_enabled(),
            "v3": av.v3, "epp_managed": av.epp_managed,
            "free_gib": round(free / GIB, 1),
            "missing": [a.key for a in missing],
        }
        _log.info("check: %s", self._state["check"])
        if missing and free < SETUP_FREE_GIB * GIB:
            return _Failed(messages_ko.error(ErrorCode.SETUP_DISK, f"free {free / GIB:.1f} GiB < {SETUP_FREE_GIB} GiB"))
        return _Done()

    def _resolve(self) -> list[repo_index.Resolved]:
        if self._resolved is None:
            repo = self._d.fetch_repo_index()
            sysimg = self._d.fetch_sysimg_index()
            resolved = [repo_index.resolve(a, sysimg if a is pins.SYSTEM_IMAGE else repo) for a in self._d.archives]
            texts = repo_index.license_texts(resolved, (repo, sysimg))   # may raise SETUP_LICENSE_NOT_STABLE
            # cache only both together: a retry with the list but no texts would pass LICENSE unasked
            self._resolved, self._license_texts = resolved, texts
            self._state["resolved"] = [{"key": r.archive.key, "url": r.url, "in_index": r.in_index,
                                        "license": r.license_id} for r in self._resolved]
            for r in self._resolved:
                if not r.in_index:
                    _log.warning("%s %s is no longer in the index; using the pinned URL and SHA-1",
                                 r.archive.key, r.archive.revision)
        return self._resolved

    def _license(self) -> _Outcome:
        records = license.load_records(self._paths)
        if not self._missing_archives() and license.covers(records, [a.package_path for a in self._d.archives]):
            return _Done()
        self._resolve()
        while True:
            records = license.load_records(self._paths)
            todo = license.pending(records, self._license_texts)
            if not todo:
                return _Done()
            with self._lock:
                self._pending_licenses = todo
            changed = license.changed_since_acceptance(records, todo[0])
            self._emit(SetupStep.LICENSE, "waiting_user",
                       problem=messages_ko.error(ErrorCode.SETUP_LICENSE_CHANGED) if changed else None)
            decision = self._wait_decision("license")
            if decision is None:
                raise dl.Cancelled()
            sha256, accepted_by = decision
            if sha256 != todo[0].sha256:
                _log.warning("license acceptance for a different text ignored")
                continue
            license.record_acceptance(self._paths, todo[0], accepted_by)
            _log.info("license %s accepted by %s", todo[0].license_id, accepted_by)
            self._emit(SetupStep.LICENSE, "running")

    def _dism_ok_this_boot(self) -> bool:
        dism = self._state.get("dism") or {}
        return bool(dism.get("ok")) and accel.same_boot(float(dism.get("boot_time", 0)), self._d.boot_time(),
                                                        dism.get("boot_id"), self._d.boot_id())

    def _whpx(self) -> _Outcome:
        state = self._d.query_whpx()
        if state is accel.WhpxState.ENABLED:
            return _Done()   # the final accel check (step 7) decides
        if self._dism_ok_this_boot():
            return _Done()   # enabled earlier in this boot; the reboot gate takes over
        previous = self._state.get("dism") or {}
        if previous.get("ok") and not previous.get("reverted_seen"):
            # dism succeeded in an earlier boot and the feature is off again: a restore program or a
            # policy undid it. Say so once (with the IT request); the next run may try UAC again.
            previous["reverted_seen"] = True
            self._save_state()
            return _Failed(messages_ko.error(ErrorCode.SETUP_WHPX_REVERTED,
                                             f"InstallState={int(state) if state is not None else '?'} after reboot"))
        self._emit(SetupStep.WHPX, "waiting_user")
        if self._wait_decision("whpx") is None:
            raise dl.Cancelled()
        self._emit(SetupStep.WHPX, "running")
        outcome = self._d.run_dism()
        verdict = whpx.classify(outcome)
        self._state["dism"] = asdict(outcome) | {
            "ok": verdict.ok, "reboot_needed": verdict.reboot_needed, "boot_time": self._d.boot_time(),
            "boot_id": self._d.boot_id(),
            "install_state_before": int(state) if state is not None else None,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self._save_state()
        _log.info("dism: %s", self._state["dism"])
        if verdict.ok:
            return _Done()
        assert verdict.error is not None
        detail = f"launched={outcome.launched} last_error={outcome.last_error} exit_code={outcome.exit_code}"
        return _Failed(messages_ko.error(verdict.error, detail), can_continue=True)

    def _download(self) -> _Outcome:
        missing = self._missing_archives()
        if not missing:
            return _Done()
        todo = [r for r in self._resolve() if r.archive in missing]
        total = sum(r.size for r in todo)
        finished = 0
        last = {"t": time.monotonic(), "bytes": -1, "emit": 0.0, "rate": 0.0}
        for r in todo:
            def progress(done: int, _size: int, base: int = finished) -> None:
                now, overall = time.monotonic(), base + done
                if last["bytes"] >= 0 and now > last["t"]:   # smoothed rate, ignores bytes resumed from .part
                    instant = (overall - last["bytes"]) / (now - last["t"])
                    last["rate"] = instant if last["rate"] == 0 else 0.8 * last["rate"] + 0.2 * instant
                last["t"], last["bytes"] = now, overall
                if now - last["emit"] < 0.5 and overall < total:
                    return
                last["emit"] = now
                rate = last["rate"]
                self._emit(SetupStep.DOWNLOAD, "running", progress=overall / total, done=overall, total=total,
                           rate=rate, eta=(total - overall) / rate if rate > 0 else None)
            self._d.download(r.url, self._paths.downloads / r.archive.filename, size=r.size, sha1=r.sha1,
                             progress=progress, cancel=self._cancel, paused=self._paused)
            finished += r.size
            last["bytes"] = -1
        return _Done()

    def _clean_work_dirs(self) -> None:
        """Leftovers of older versions, which extracted and then renamed (tmp\\extract_*, tmp\\old_*). Best effort."""
        for d in [*self._paths.tmp.glob("extract_*"), *self._paths.tmp.glob("old_*")]:
            try:
                extract.remove_tree(d, timeout_s=30)
            except (OrcusFailure, OSError) as e:
                _log.warning("could not remove %s yet: %s", d, e)

    @staticmethod
    def _zip_size(zip_path: Path, top: str) -> int:
        try:
            return extract.uncompressed_size(zip_path, top)
        except (zipfile.BadZipFile, OSError):
            return zip_path.stat().st_size

    def _extract(self) -> _Outcome:
        self._clean_work_dirs()
        installed = self._state.setdefault("installed", {})
        todo: list[tuple[pins.Archive, Path, int]] = []
        for archive in self._missing_archives():
            zip_path = self._paths.downloads / archive.filename
            if not zip_path.exists():
                return _Failed(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{zip_path.name} not downloaded"))
            if not self._d.verify_zip(zip_path, archive):   # checked at download; it may have changed since
                zip_path.unlink(missing_ok=True)
                return _Failed(messages_ko.error(ErrorCode.SETUP_CHECKSUM, f"{zip_path.name} changed after download"))
            todo.append((archive, zip_path, self._zip_size(zip_path, archive.zip_top)))
        grand_total = sum(size for _, _, size in todo)   # one progress bar for all archives
        base = 0
        last_emit = [0.0]
        for archive, zip_path, size in todo:
            def progress(done: int, _total: int, base: int = base) -> None:
                now, overall = time.monotonic(), base + done
                if now - last_emit[0] < 0.3 and overall < grand_total:
                    return
                last_emit[0] = now
                self._emit(SetupStep.EXTRACT, "running", progress=overall / grand_total if grand_total else None,
                           done=overall, total=grand_total)
            result = self._d.install_archive(self._paths, archive, zip_path, progress=progress, cancel=self._cancel)
            installed[archive.key] = {"revision": archive.revision, "found": result.found,
                                      "optional_missing": list(result.optional_missing)}
            if result.optional_missing:
                _log.warning("%s: optional files missing: %s", archive.key, result.optional_missing)
            self._save_state()
            try:   # the zip is no longer needed; a scanner may still be reading it
                extract.retry_locked(lambda: zip_path.unlink(missing_ok=True), zip_path, timeout_s=30)
            except OrcusFailure:
                _log.warning("could not delete %s yet; the next run removes it", zip_path)
            base += size
        self._paths.platforms.mkdir(parents=True, exist_ok=True)   # SDK-root validation needs it
        return _Done()

    def _reboot(self) -> _Outcome:
        dism = self._state.get("dism") or {}
        if dism.get("reboot_needed") and self._dism_ok_this_boot():
            self._d.register_resume()
            self._emit(SetupStep.REBOOT, "waiting_user")
            return _Stop()
        self._d.unregister_resume()
        return _Done()

    def _accel(self) -> _Outcome:
        now, now_id = self._d.boot_time(), self._d.boot_id()
        cached = self._state.get("accel") or {}
        if cached.get("usable") and accel.same_boot(float(cached.get("boot_time", 0)), now, cached.get("boot_id"),
                                                    now_id):
            return _Done()
        self._d.ensure_job()   # the first SDK child starts here (§1.2)
        report = self._d.emulator_check()
        self._state["accel"] = asdict(report) | {"boot_time": now, "boot_id": now_id}
        self._save_state()
        _log.info("emulator-check accel: code=%s usable=%s\n%s", report.code, report.usable, report.text)
        if report.usable:
            return _Done()
        dism = self._state.get("dism") or {}
        code = accel.classify_accel_failure(
            reboot_is_pending=self._d.reboot_pending(),
            dism_succeeded_at_boot=float(dism["boot_time"]) if dism.get("ok") else None,
            current_boot=now, dism_boot_id=dism.get("boot_id"), current_boot_id=now_id)
        return _Failed(messages_ko.error(code, report.text[-600:]))

    def _avd(self) -> _Outcome:
        engine = self._d.settings.engine
        if not any(self._paths.avd_data.glob("userdata-qemu.img*")):
            need = int(1.2 * USERDATA_GIB * GIB) + engine.ram_mb * (1 << 20)
            if self._d.disk_free() < need:
                return _Failed(messages_ko.error(ErrorCode.SETUP_DISK, f"need {need / GIB:.1f} GiB for the phone"))
        result = write_avd(self._paths, engine)
        if not self._paths.adbkey.exists():
            out = self._d.adb_keygen()
            if out.code != 0 or not self._paths.adbkey.exists() or not self._paths.adbkey.with_suffix(".pub").exists():
                return _Failed(messages_ko.error(ErrorCode.SETUP_QUARANTINE,   # adb.exe blocked is the likely cause
                                                 f"adb keygen failed: {out.code} {out.err.strip()[-300:]}"))
        self._state["avd"] = {"config_sha256": result.config_sha256, "changed": result.changed}
        self._save_state()
        return _Done()
