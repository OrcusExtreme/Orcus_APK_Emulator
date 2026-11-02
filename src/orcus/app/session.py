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
from orcus.common.i18n import t
from orcus.common.paths import OrcusPaths
from orcus.common.pins import EMULATOR, EMULATOR_BUILD, SYSTEM_IMAGE
from orcus.contracts import (
    ApplyResult,
    DeviceEndpoint,
    DeviceSession,
    EngineNotReady,
    EngineState,
    Settings,
    media_folder,
)
from orcus.device import session as device_session
from orcus.engine import camera, provision, readonly
from orcus.engine.manager import EngineManager
from orcus.engine.seams import Seams
from orcus.setup.steps import SetupService

from . import compose, diag

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
        self._gui = owner_kind == "gui"   # setup started here resumes in the window after a reboot
        self._engine = EngineManager(paths, self.settings, seams or Seams.real(paths), self._connect,
                                     owner_kind=owner_kind)
        self._apps = ApkPipeline(self._engine, paths.staging, lambda: self.settings().apk,
                                 mic_allowed=lambda pkg: pkg in self.settings().privacy.mic_allowed_packages)

    # --- wiring ---------------------------------------------------------------------------------------------------
    def _connect(self, endpoint: DeviceEndpoint) -> DeviceSession:
        """The DeviceSessionFactory the engine calls in SESSION_INIT. Logcat comes through the engine's adb:
        gRPC streamLogcat goes silent after a snapshot load ([Reset phone])."""
        presets = self.settings().sensors.location_presets   # SESSION_INIT put the phone at the first one
        return device_session.connect(endpoint, location=self._locate, logcat_source=self._engine.logcat,
                                      scroll=self._scroll, home=(presets[0][0], presets[0][1]) if presets else None)

    def _locate(self, lat: float, lon: float, alt_m: float) -> None:
        """device-controls thread: the framework's test providers, through adb (S7). A repeat that meets a reset
        is dropped: the new session starts at Seoul City Hall again (§6.6)."""
        try:
            sh = self._engine.shell()
            for cmd in provision.location_commands(lat, lon):
                sh.shell(cmd.split(), timeout_s=10)
        except EngineNotReady as e:
            _log.info("location dropped: %s", e)

    def _scroll(self, x: int, y: int, amount: float) -> None:
        """device-wheel thread: ACTION_SCROLL through `input` (the guest has no wheel device, §9.3)."""
        try:
            self._engine.shell().shell(["cmd", "input", "mouse", "scroll", str(x), str(y), "--axis",
                                        f"VSCROLL,{amount:g}"], timeout_s=5)
        except EngineNotReady as e:
            _log.info("scroll dropped: %s", e)

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
                self._setup = compose.build_setup(self._paths, self._settings, gui=self._gui)
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
        none = t("없음")
        lines = [t("Orcus APK Emulator 보고서 ({when})", when=time.strftime("%Y-%m-%d %H:%M:%S")),
                 f"Orcus {__version__} · Python {platform.python_version()} · Windows {platform.version()}",
                 t("에뮬레이터 {revision} (build {build}) · 이미지 {image} r{image_revision}",
                   revision=EMULATOR.revision, build=EMULATOR_BUILD, image=SYSTEM_IMAGE.package_path,
                   image_revision=SYSTEM_IMAGE.revision),
                 t("엔진: {state} · {mode} · epoch {epoch} ({cause}) · 경고 {warnings}", state=st.state, mode=st.mode,
                   epoch=st.epoch, cause=st.epoch_cause, warnings=", ".join(st.warnings) or none)]
        if st.error is not None:
            lines.append(t("엔진 오류: {code} {detail}", code=st.error.code, detail=st.error.detail))
        c = st.caps
        if c is not None:
            lines.append(t("기기: API {api} · {abis} · {width}x{height} {dpi}dpi · {hypervisor} · GPU {gpu} · "
                           "{renderer} · TTS {tts} · 음성 인식 {speech}", api=c.api, abis=",".join(c.abis),
                           width=c.panel[0], height=c.panel[1], dpi=c.panel[2], hypervisor=c.hypervisor,
                           gpu=c.gpu_mode, renderer=c.renderer, tts=c.tts_engine or none,
                           speech=t("있음") if c.speech_recognizer else none))
        return "\n".join([*lines, *self._apps.report_lines()])

    def set_camera_image(self, png: bytes | None) -> None:
        camera.put(self._paths, png)

    def make_diagnostics(self) -> Path:
        doctor = [f"{line.status:<4} {line.name:<20} {line.detail}" for line in compose.doctor(self._paths,
                                                                                         self.settings())]
        return diag.make_bundle(self._paths, self.report(), doctor)

    def open_paths(self, paths: Sequence[Path]) -> None:
        """Pictures, sounds and videos go into the phone, all of them; otherwise the last file is an app (the
        newest drop wins anyway: only it is worth staging)."""
        files = [Path(p) for p in paths]
        if files and all(media_folder(p) for p in files):
            self._apps.push_media(files)
        elif files:
            self._apps.submit(files[-1])

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
