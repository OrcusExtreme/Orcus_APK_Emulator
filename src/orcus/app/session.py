"""app.Session: the one object the GUI and orcus-cli talk to (SessionApi and HeadlessSession, ARCHITECTURE.md §3).

It wires the engine (EngineManager), the device (a gRPC DeviceSession per epoch, whose location sink needs an
adb shell) and the APK pipeline together; nothing else builds them (layering rule §2). The caller makes the
process's Job object first (`compose.ensure_job()`, §1.2), before the engine starts any child.
"""

import platform
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from orcus import __version__
from orcus.apk.service import Service as ApkPipeline
from orcus.common import config, log
from orcus.common.paths import OrcusPaths
from orcus.common.pins import EMULATOR, EMULATOR_BUILD, SYSTEM_IMAGE
from orcus.contracts import (
    ApplyResult,
    DeviceEndpoint,
    DeviceSession,
    EngineNotReady,
    EngineState,
    Settings,
)
from orcus.device import session as device_session
from orcus.engine import provision, readonly
from orcus.engine.manager import EngineManager
from orcus.engine.seams import Seams
from orcus.setup.steps import SetupService

from . import compose

_log = log.get("app.session")
IDLE = (EngineState.STOPPED, EngineState.FAILED, EngineState.NOT_INSTALLED)
STOP_WAIT_S = 15.0


class Session:
    """SessionApi + HeadlessSession. Every method returns at once except shutdown() (bounded, at exit)."""

    def __init__(self, paths: OrcusPaths, settings: Settings, *, owner_kind: str = "gui",
                 seams: Seams | None = None) -> None:
        self._paths = paths
        self._settings = settings
        self._lock = threading.Lock()
        self._setup: SetupService | None = None
        self._engine = EngineManager(paths, self.settings, seams or Seams.real(paths), self._connect,
                                     owner_kind=owner_kind)
        self._apps = ApkPipeline(self._engine, paths.staging, lambda: self.settings().apk)

    # --- wiring ---------------------------------------------------------------------------------------------------
    def _connect(self, endpoint: DeviceEndpoint) -> DeviceSession:
        """The DeviceSessionFactory the engine calls in SESSION_INIT. Logcat comes through the engine's adb:
        gRPC streamLogcat goes silent after a snapshot load ([Reset phone], Phase 3a)."""
        return device_session.connect(endpoint, location=self._locate, logcat_source=self._engine.logcat)

    def _locate(self, lat: float, lon: float, alt_m: float) -> None:
        """device-controls thread: the framework's test providers, through adb (S7)."""
        sh = self._engine.shell()
        for cmd in provision.location_commands(lat, lon):
            sh.shell(cmd.split(), timeout_s=10)

    # --- SessionApi -----------------------------------------------------------------------------------------------
    @property
    def engine(self) -> EngineManager:
        return self._engine

    @property
    def apps(self) -> ApkPipeline:
        return self._apps

    @property
    def setup(self) -> SetupService:
        with self._lock:
            if self._setup is None:
                self._setup = compose.build_setup(self._paths, self._settings)
            return self._setup

    def device(self) -> DeviceSession | None:
        try:
            return self._engine.session()
        except EngineNotReady:
            return None

    def settings(self) -> Settings:
        return self._settings

    def preview_settings(self, changes: Mapping[str, object]) -> ApplyResult:
        old = self._settings
        return config.classify(old, config.apply(old, changes))

    def update_settings(self, changes: Mapping[str, object]) -> ApplyResult:
        """Saves to state\\settings.json. The engine reads settings when it next needs them; a GPU mode change
        takes effect through the rebuild the GUI confirms (ApplyResult.needs_rebuild)."""
        with self._lock:
            old = self._settings
            new = config.apply(old, changes)
            config.save_overrides(self._paths, changes)
            self._settings = new
        return config.classify(old, new)

    def report(self) -> str:
        """[Copy report] (§7.9)."""
        st = self._engine.status()
        lines = [f"Orcus APK Emulator 보고서 ({time.strftime('%Y-%m-%d %H:%M:%S')})",
                 f"Orcus {__version__} · Python {platform.python_version()} · Windows {platform.version()}",
                 f"에뮬레이터 {EMULATOR.revision} (build {EMULATOR_BUILD}) · 이미지 {SYSTEM_IMAGE.package_path} "
                 f"r{SYSTEM_IMAGE.revision}",
                 f"엔진: {st.state} · {st.mode} · epoch {st.epoch} ({st.epoch_cause}) · "
                 f"경고 {', '.join(st.warnings) or '없음'}"]
        if st.error is not None:
            lines.append(f"엔진 오류: {st.error.code} {st.error.detail}")
        c = st.caps
        if c is not None:
            lines.append(f"기기: API {c.api} · {','.join(c.abis)} · {c.panel[0]}x{c.panel[1]} {c.panel[2]}dpi · "
                         f"{c.hypervisor} · GPU {c.gpu_mode} · {c.renderer} · TTS {c.tts_engine or '없음'} · "
                         f"음성 인식 {'있음' if c.speech_recognizer else '없음'}")
        return "\n".join([*lines, *self._apps.report_lines()])

    def open_paths(self, paths: Sequence[Path]) -> None:
        if paths:   # the newest drop wins anyway: only the last one is worth staging
            self._apps.submit(Path(paths[-1]))

    def shutdown(self, *, fast: bool = False) -> None:
        """§4 shutdown order from step 2: cancel the APK pipeline, stop the engine (it closes the device session
        and releases a held finger), then the private adb server. Bounded: ~2 s fast, ~15 s otherwise."""
        self._apps.close(fast=fast)
        if not fast and self._engine.status().state not in IDLE:
            self._engine.stop()
            end = time.monotonic() + STOP_WAIT_S
            while self._engine.status().state not in IDLE and time.monotonic() < end:
                time.sleep(0.05)
        self._engine.close()

    # --- HeadlessSession ------------------------------------------------------------------------------------------
    def attach_readonly(self) -> readonly.ReadOnlyVm | None:
        return compose.attach_readonly(self._paths)
