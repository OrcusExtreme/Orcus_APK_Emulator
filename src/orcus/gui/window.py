"""Main window (ARCHITECTURE.md §10.1): toolbar, the phone with its overlays, [App]/[Sensors]/[Log] panels,
the Korean input bar under the phone, status bar.

Everything arrives through the Bridge on the main thread. The window only calls SessionApi and DeviceControls,
whose methods all return at once. Feedback goes to one channel per severity (§10.2, the code -> channel table
in messages_ko); banners are the engine's warnings plus the running app's (RunEvent.warnings). Sentences go
through t() (common.i18n): Korean or English.
"""

import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QGuiApplication, \
    QImage, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from orcus.common import messages_ko, win32
from orcus.common.i18n import N_, t
from orcus.common.messages_ko import Channel, text
from orcus.contracts import (
    ApkInfo,
    DeviceControls,
    DeviceSession,
    EngineState,
    EngineStatus,
    EpochCause,
    ErrorCode,
    HwKey,
    MediaResult,
    OrcusError,
    PhoneWorld,
    RemedyAction,
    Rotation,
    RunEvent,
    RunStage,
    Sensitivity,
    SessionApi,
    SessionMode,
    media_folder,
)

from . import dialogs
from .bridge import Bridge
from .frame_pump import FramePump
from .hangul import HangulBar
from .keymap import SHORTCUTS
from .overlays import Banner, EnginePanel, Overlay, Toast
from .panels import AppPanel, LogPanel
from .phone import PhoneView
from .screen import ScreenView, fit
from .sensors import SHAKE_REPEAT_MS, SensorPanel
from .settings_dialog import SettingsDialog, focus_gpu
from .wizard import SetupWizard

PANEL_W = 360
WINDOW_CHROME_H = 192   # title bar, toolbar, banner room, status bar (§9.1: ~256 px with the bezel and nav row)
BUSY = (RunStage.STAGING, RunStage.INSPECTING, RunStage.CHECKING, RunStage.INSTALLING, RunStage.LAUNCHING)
USED = (RunStage.INSTALLING, RunStage.LAUNCHING, RunStage.RUNNING)   # the app may have written data
LANDSCAPES = (Rotation.LANDSCAPE, Rotation.REVERSE_LANDSCAPE)
RECORD_AUDIO = "android.permission.RECORD_AUDIO"
CAMERA_MAX_PX = 1280   # the camera's picture is scaled down to this a side
MEDIA_WORDS = {"Pictures": (N_("사진"), N_("ImagePicker로 고를 수 있습니다")),   # dropped files (§7.1)
               "Music": (N_("음악"), N_("Sound·Player의 Source에 이 경로를 쓰세요")),
               "Movies": (N_("동영상"), N_("VideoPlayer의 Source에 이 경로를 쓰세요"))}
CRASH_NOTICE = {SessionMode.GOLDEN: N_("휴대폰이 멈춰서 다시 켰습니다. 저장된 앱 데이터는 처음 상태로 돌아갔습니다."),
                SessionMode.COLD: N_("휴대폰이 멈춰서 다시 켰습니다."),
                SessionMode.KEEP_STATE: N_("휴대폰이 멈춰서 마지막으로 저장한 상태로 다시 켰습니다.")}
CLOSE_NOTICE = N_("이번에 실행한 앱의 저장 데이터(TinyDB 등)는 닫으면 지워집니다.")   # §6.1, GOLDEN only
KEEP_ONCE = N_("이번에는 휴대폰 유지하고 닫기")   # §6.1
NO_DISPLAY_CHIP = N_("이번만 느린 시작 — 초기화하면 깨끗한 휴대폰으로")   # COLD boot that kept the golden
MODE_CHIP = {SessionMode.GOLDEN: N_("깨끗한 휴대폰 모드 — 닫으면 처음 상태로"),
             SessionMode.COLD: N_("느린 그래픽 모드 · 휴대폰 상태가 유지됨"), SessionMode.KEEP_STATE: N_("휴대폰 유지 모드")}
# RemedyAction -> handler method name; the unit test checks every action has one (§3)
REMEDIES: dict[RemedyAction, str] = {
    RemedyAction.RETRY: "retry_engine", RemedyAction.ENABLE_WHPX: "open_setup",
    RemedyAction.COPY_IT_REQUEST: "copy_it_request", RemedyAction.REPAIR_SETUP: "open_setup",
    RemedyAction.OPEN_LOG: "open_logs", RemedyAction.RESET_PHONE: "reset_phone", RemedyAction.RUN_AGAIN: "rerun",
    RemedyAction.GRANT_ALL: "grant_all", RemedyAction.CHANGE_GPU_MODE: "gpu_dialog",
    RemedyAction.FREE_DISK: "explain_disk", RemedyAction.CONTINUE_ANYWAY: "rerun",
    RemedyAction.MATCH_ORIENTATION: "match_orientation", RemedyAction.RETRY_GPU: "retry_gpu",
}


def host_has_mic() -> bool:
    try:
        return win32.capture_device_count() > 0
    except OSError:   # winmm missing (Windows N without media features): let the user try
        return True


class MainWindow(QMainWindow):
    def __init__(self, session: SessionApi, bridge: Bridge, logs_dir: Path) -> None:
        super().__init__()
        self.s = session
        self.bridge = bridge
        self.logs_dir = logs_dir
        self.quiet_close = False   # logoff and other ends that must not ask (gui.app sets it)
        self._wizard: SetupWizard | None = None
        self.setWindowTitle("Orcus APK Emulator")
        if not QApplication.windowIcon().isNull():
            self.setWindowIcon(QApplication.windowIcon())
        self.setAcceptDrops(True)
        self.pump = FramePump(bridge)
        self.screen_view = ScreenView(self.pump, lambda: self.s.settings().input)
        self.phone = PhoneView(self.screen_view, self._phone_key, self._phone_action)
        self.overlay = Overlay(self.screen_view)
        self.overlay.clicked.connect(self._overlay_clicked)
        self.toast = Toast(self.screen_view)
        self.engine_panel = EnginePanel(self.screen_view)
        self.engine_panel.remedy.connect(self.remedy)
        self.engine_panel.open_log.connect(self.open_logs)
        self.engine_panel.copy_report.connect(self.copy_report)
        self.banners = QVBoxLayout()
        self.banner_widgets: dict[str, Banner] = {}
        self.hangul_bar = HangulBar()
        self.hangul_bar.send.connect(self._send_text)
        self.hangul_bar.closed.connect(lambda: self.screen_view.setFocus())
        self.app_panel = AppPanel()
        self.app_panel.action.connect(self._app_action)
        self.app_panel.installed.connect(self._installed_action)
        self.sensors = SensorPanel(session.settings().sensors.location_presets)
        self.sensors.shake.connect(self.shake)
        self.sensors.sensitivity_changed.connect(self._sensitivity_chosen)
        self.sensors.rotate.connect(self.rotate)
        self.sensors.match.connect(self.match_orientation)
        self.sensors.tilt.connect(self._tilt)
        self.sensors.locate.connect(self._locate)
        self.sensors.microphone.connect(self._microphone)
        self.sensors.light.connect(self._light)
        self.sensors.proximity.connect(self._proximity)
        self.sensors.battery.connect(self._battery)
        self.sensors.sms.connect(self._sms)
        self.sensors.call.connect(self._call)
        self.sensors.camera.connect(self._camera)
        self.log_panel = LogPanel()
        self.log_panel.remedy.connect(lambda err: self.remedy(err.remedy.action) if err.remedy else None)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.app_panel, t("앱"))
        self.tabs.addTab(self.sensors, t("센서"))
        self.tabs.addTab(self.log_panel, t("로그"))
        self.tabs.setFixedWidth(PANEL_W)
        left = QVBoxLayout()
        left.addLayout(self.banners)
        left.addWidget(self.phone, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.hangul_bar)
        left.addStretch(1)
        row = QHBoxLayout()
        row.addLayout(left, 1)
        row.addWidget(self.tabs)
        central = QWidget()
        central.setLayout(row)
        self.setCentralWidget(central)
        self.status_engine = QLabel()
        self.status_app = QLabel()   # carries the app's own label: never rich text
        for label in (self.status_engine, self.status_app):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_mic = QLabel()
        self.statusBar().addWidget(self.status_engine, 1)
        self.statusBar().addPermanentWidget(self.status_app)
        self.statusBar().addPermanentWidget(self.status_mic)
        self._toolbar()
        self._shortcuts()
        self._status: EngineStatus = session.engine.status()
        self._epoch = -1
        self._device: DeviceSession | None = None
        self._landscape = False
        self._retry_frames = 0
        self._app_name = ""
        self._busy_run = False
        self._info: ApkInfo | None = None      # the app of the newest run (sensitivity, mic consent)
        self._run_warnings: tuple[ErrorCode, ...] = ()
        self._used_app = False                 # installed or run an app this session: ask at close (§6.1)
        self._run_stage: RunStage | None = None
        self._last_shake = 0.0
        self._host_mic = host_has_mic()
        self._show_mic()
        bridge.engine.connect(self.on_engine)
        bridge.run.connect(self.on_run)
        bridge.log.connect(self.log_panel.append_log)
        bridge.vibrated.connect(self.screen_view.buzz)
        bridge.media.connect(self._media_done)
        bridge.apps.connect(self.app_panel.show_installed)
        bridge.diag.connect(self._diag_done)
        bridge.frame.connect(self._on_frame)
        bridge.frames_ended.connect(self._on_frames_ended)
        bridge.shot.connect(self._on_shot)
        bridge.paths.connect(lambda paths: self.s.open_paths([Path(p) for p in paths]))
        self.fit_phone()
        QTimer.singleShot(0, lambda: self.on_engine(self.s.engine.status()))

    # --- layout (§9.1) ------------------------------------------------------------------------------------------
    def fit_phone(self) -> None:
        scr = self.screen() or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry().size()
        dpr = scr.devicePixelRatio()
        chrome = PhoneView.chrome()
        with_panel = QSize(chrome.width() + PANEL_W + 48, chrome.height() + WINDOW_CHROME_H)
        size = fit(avail, chrome=with_panel, landscape=self._landscape, dpr=dpr)
        alone = fit(avail, chrome=QSize(chrome.width() + 48, chrome.height() + WINDOW_CHROME_H),
                    landscape=self._landscape, dpr=dpr)
        self.tabs.setVisible(size.height() >= alone.height() * 0.9)   # fold the panel when the phone would shrink
        if not self.tabs.isVisible():
            size = alone
        self.phone.set_screen_size(size)
        self.hangul_bar.setFixedWidth(self.phone.sizeHint().width())
        for w in (self.overlay, self.engine_panel):
            w.setGeometry(0, 0, size.width(), size.height())
        if self._device is not None:
            self._device.frames.retarget(round(size.width() * dpr), round(size.height() * dpr))
        self.adjustSize()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        for w in (self.overlay, self.engine_panel):
            w.setGeometry(0, 0, self.screen_view.width(), self.screen_view.height())

    def _phys(self) -> tuple[int, int]:
        dpr = self.screen_view.devicePixelRatioF()
        return round(self.screen_view.width() * dpr), round(self.screen_view.height() * dpr)

    # --- toolbar and shortcuts ----------------------------------------------------------------------------------------
    def _toolbar(self) -> None:
        bar = QToolBar(t("도구"))
        bar.setMovable(False)
        self.addToolBar(bar)
        for label, slot, tip in ((t("APK 열기"), self.open_apk, "Ctrl+O"), (t("다시 실행"), self.rerun, "F5"),
                                 (t("앱 중지"), self.s.apps.stop_app, ""),
                                 (t("데이터 지우고 실행"), self.s.apps.clear_data, ""),
                                 (t("휴대폰 초기화"), self.reset_phone, t("확인 후 초기화")),
                                 (t("스크린샷"), self.screenshot, ""), (t("설정"), self.settings_dialog, ""),
                                 (t("도움말"), self.help, "")):
            act = QAction(label, self)
            act.setToolTip(f"{label} ({tip})" if tip else label)
            act.triggered.connect(slot)
            bar.addAction(act)

    def _shortcuts(self) -> None:
        actions = {"open_apk": self.open_apk, "rerun": self.rerun,
                   "home": lambda: self._phone_key(HwKey.HOME, False),
                   "recents": lambda: self._phone_key(HwKey.RECENTS, False),
                   "shake": self.shake, "rotate_left": lambda: self.rotate(1), "rotate_right": lambda: self.rotate(-1),
                   "hangul_bar": self.toggle_hangul, "overlay": self._toggle_overlay}
        for name, keys in SHORTCUTS.items():
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(actions[name])

    def _toggle_overlay(self) -> None:
        self.screen_view.overlay = not self.screen_view.overlay
        self.screen_view.update()

    # --- engine --------------------------------------------------------------------------------------------------------
    def on_engine(self, st: EngineStatus) -> None:
        self._status = st
        android = "Android 16" if st.caps and st.caps.api == 36 else (f"API {st.caps.api}" if st.caps else "")
        gpu = f"GPU {st.caps.gpu_mode}" if st.caps else ""
        chip = t(NO_DISPLAY_CHIP if ErrorCode.ENGINE_NO_DISPLAY in st.warnings else MODE_CHIP[st.mode])
        parts = [text(st.text_key), android, gpu, chip if st.state == EngineState.READY else ""]
        self.status_engine.setText("● " + " · ".join(p for p in parts if p))
        if st.state == EngineState.READY:
            self.engine_panel.hide()
            if st.epoch != self._epoch:
                self._epoch = st.epoch
                self._attach_device()
                if st.epoch_cause == EpochCause.CRASH_RESTART:
                    self.toast.show_text(t(CRASH_NOTICE[st.mode]), 6000)
                elif st.epoch_cause in (EpochCause.RESET, EpochCause.RESET_RECOVERY):
                    self.toast.show_text(t("휴대폰 초기화 완료"))
            if not self._busy_run:
                self.overlay.hide()
            if not st.display_on:
                self.overlay.show_text(t("클릭하면 화면이 켜집니다"))
        elif st.state in (EngineState.FAILED, EngineState.NOT_INSTALLED):
            self._detach_device()
            self.overlay.hide()
            self.engine_panel.show_error(st.error or messages_ko.error(ErrorCode.PRE_NOT_INSTALLED))
        else:
            if st.state in (EngineState.STOPPING, EngineState.STOPPED, EngineState.PROVISIONING):
                self._detach_device()
            self.engine_panel.hide()
            self.overlay.show_text(text(st.text_key), st.progress)
        self._sync_banners(st.warnings)

    def _sync_banners(self, warnings: tuple[ErrorCode, ...]) -> None:
        wanted = {str(c): c for c in (*warnings, *self._run_warnings)}
        for key in list(self.banner_widgets):
            if key not in wanted:
                self.banner_widgets.pop(key).deleteLater()
        for key, code in wanted.items():
            if key in self.banner_widgets:
                continue
            spec = messages_ko.spec(code)
            action = None
            if spec.remedy is not None:
                act = spec.remedy
                # clicked(bool) fills the first parameter: `lambda a=act:` got False and the button did nothing
                action = (text(f"remedy.{act}"), lambda _=False, a=act: self.remedy(a))
            b = Banner(spec.text, action=action, on_close=lambda _=False, k=key: self._forget_banner(k))
            self.banner_widgets[key] = b
            self.banners.addWidget(b)

    def _forget_banner(self, key: str) -> None:
        self.banner_widgets.pop(key, None)

    def _attach_device(self) -> None:
        self._detach_device()
        dev = self.s.device()
        if dev is None:
            return
        self._device = dev
        self._retry_frames = 0
        self.screen_view.set_input(dev.input)
        w, h = self._phys()
        dev.frames.start(w, h, self.pump.on_frame, self.bridge.on_frames_ended)
        # a new phone: level, at the first location preset, microphone off (SESSION_INIT, §6.6); its posture,
        # light and battery as the phone has them (a kept phone comes back as it was saved)
        self.sensors.set_device(dev.controls is not None)
        self.sensors.set_posture(dev.controls.posture if dev.controls else Rotation.PORTRAIT)
        self.sensors.set_world(dev.controls.world if dev.controls else PhoneWorld())
        self.sensors.center_tilt()
        self.sensors.reset_location()
        self._show_mic()

    def _detach_device(self) -> None:
        if self._device is None:
            return
        self.screen_view.release()
        self.screen_view.set_input(None)
        self._device.frames.stop()
        self._device = None
        self.pump.clear()
        self.screen_view.clear()
        self.sensors.set_device(False)
        self._show_mic()

    def _controls(self) -> DeviceControls | None:
        return self._device.controls if self._device is not None else None

    def _on_frame(self) -> None:
        turned = self.screen_view.show_frame()
        if turned is not None:
            self.sensors.set_posture(turned)   # frames follow the posture, whoever turned it (§9.7)
            if (turned in LANDSCAPES) != self._landscape:
                self._landscape = turned in LANDSCAPES   # the window and bezel turn with the frame
                self.fit_phone()

    def _on_frames_ended(self, err: OrcusError | None) -> None:
        if self._device is None or self._status.state != EngineState.READY:
            return   # the next epoch reopens it
        self.screen_view.release()
        self._retry_frames += 1
        delay = min(5000, 250 * 2 ** min(self._retry_frames, 5))
        QTimer.singleShot(delay, self._reopen_frames)

    def _reopen_frames(self) -> None:
        if self._device is not None and self._status.state == EngineState.READY:
            w, h = self._phys()
            self._device.frames.start(w, h, self.pump.on_frame, self.bridge.on_frames_ended)

    def _phone_key(self, key: HwKey, long_press: bool) -> None:
        if self._device is not None:
            self._device.input.key(key, long_press=long_press)
            self.screen_view.setFocus()

    def _phone_action(self, name: str) -> None:
        match name:
            case "rotate_left":
                self.rotate(1)
            case "rotate_right":
                self.rotate(-1)
            case "shake":
                self.shake()
            case "hangul":
                self.toggle_hangul()
            case _:
                pass

    def _overlay_clicked(self) -> None:
        if self._status.state == EngineState.READY and not self._status.display_on:
            self._phone_key(HwKey.POWER, False)
            self.overlay.hide()

    # --- sensors (§9.7, §9.8) ------------------------------------------------------------------------------------
    def shake(self) -> None:
        """One pulse; F6 held repeats with the keyboard (30 a second), so pulses keep 450 ms apart like the button."""
        c = self._controls()
        now = time.monotonic()
        if c is None or now - self._last_shake < SHAKE_REPEAT_MS / 1000 - 0.03:
            return
        self._last_shake = now
        c.shake(self.sensors.sensitivity())

    def rotate(self, step: int) -> None:
        """+1 turns the phone left (counter-clockwise: z += 90°), -1 right; gradual, from the newest posture."""
        c = self._controls()
        if c is None:
            return
        c.rotate(Rotation((c.posture + step) % 4))
        self.sensors.center_tilt()   # the turn also levels the phone

    def match_orientation(self) -> None:
        self.s.apps.match_orientation()

    def _tilt(self, pitch: float, roll: float) -> None:
        c = self._controls()
        if c is not None:
            c.tilt(pitch, roll)

    def _locate(self, lat: float, lon: float) -> None:
        c = self._controls()
        if c is not None:
            c.set_location(lat, lon)
            self.toast.show_text(t("위치를 보냈습니다. 앱에 닿기까지 몇 초 걸립니다."))

    def _light(self, lux: float) -> None:
        c = self._controls()
        if c is not None:
            c.set_light(lux)

    def _proximity(self, cm: float) -> None:
        c = self._controls()
        if c is not None:
            c.set_proximity(cm)

    def _battery(self, level: int, charging: bool) -> None:
        c = self._controls()
        if c is not None:
            c.set_battery(level, charging)

    def _sms(self, sender: str, text: str) -> None:
        c = self._controls()
        if c is not None:
            c.receive_sms(sender, text)
            self.toast.show_text(t("문자를 보냈습니다."))

    def _call(self, number: str, ring: bool) -> None:
        c = self._controls()
        if c is None:
            return
        if ring:
            c.call_in(number)
            self.toast.show_text(t("전화가 옵니다. 받거나 거절하려면 휴대폰 화면의 알림을 누르세요."))
        else:
            c.call_end(number)

    def _camera(self, pick: bool) -> None:
        """The camera's picture: a PNG of at most 1280 px a side, or the color bars."""
        if not pick:
            self._put_camera(None, t("기본 그림 (색 막대)"))
            return
        name, _ = QFileDialog.getOpenFileName(self, t("카메라에 보여 줄 그림"), "",
                                              t("그림 (*.png *.jpg *.jpeg *.bmp *.gif *.webp)"))
        if not name:
            return
        img = QImage(name)
        if img.isNull():
            QMessageBox.warning(self, t("카메라"), t("그림 파일을 읽지 못했습니다."))
            return
        if max(img.width(), img.height()) > CAMERA_MAX_PX:
            img = img.scaled(CAMERA_MAX_PX, CAMERA_MAX_PX, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")   # type: ignore[call-overload]  # the stub wants bytes; PySide6 takes only str
        self._put_camera(bytes(buf.data().data()), Path(name).name)

    def _put_camera(self, png: bytes | None, words: str) -> None:
        try:
            self.s.set_camera_image(png)
        except OSError as e:
            QMessageBox.warning(self, t("카메라"), t("그림을 넣지 못했습니다: {error}", error=e))
            return
        self.sensors.camera_label.setText(t("지금: {what}", what=words))
        self.toast.show_text(t("카메라 그림을 바꿨습니다. 앱이 카메라를 다시 열면 보입니다."))

    def _sensitivity_chosen(self, sensitivity: Sensitivity) -> None:
        if self._info is None:
            return
        table = {k: v.value for k, v in self.s.settings().sensors.shake_sensitivity.items()}
        table[self._info.package] = sensitivity.value
        self._save({"sensors.shake_sensitivity": table})

    def _app_uses_mic(self) -> bool:
        return self._info is not None and RECORD_AUDIO in self._info.permissions

    def _microphone(self, on: bool) -> None:
        """Turned by the user: remembered for the current app when it records (§9.8), applied at its next runs."""
        c = self._controls()
        if c is None:
            return
        c.set_microphone(on)
        if self._info is not None and self._app_uses_mic():
            pkg = self._info.package
            allowed = [p for p in self.s.settings().privacy.mic_allowed_packages if p != pkg]
            self._save({"privacy.mic_allowed_packages": [*allowed, pkg] if on else allowed})
        self._show_mic()

    def _show_mic(self) -> None:
        c = self._controls()
        on = c is not None and c.microphone
        self.sensors.set_mic(on, host_mic=self._host_mic, app_uses_mic=self._app_uses_mic())
        if not self._host_mic:
            self.status_mic.setText(t("마이크 없음"))
            self.status_mic.setStyleSheet("color:#7b8794")
        elif on:
            self.status_mic.setText(t("마이크 켜짐"))
            self.status_mic.setStyleSheet("color:#b3261e;font-weight:bold")
        else:
            self.status_mic.setText(t("마이크 꺼짐"))
            self.status_mic.setStyleSheet("")

    def _app_changed(self, info: ApkInfo) -> None:
        sensors = self.s.settings().sensors
        self.sensors.set_sensitivity(sensors.shake_sensitivity.get(info.package, sensors.shake_default))
        self._show_mic()

    def _save(self, changes: dict[str, object]) -> None:
        try:
            self.s.update_settings(changes)
        except (OSError, ValueError) as e:
            self.toast.show_text(t("설정을 저장하지 못했습니다: {error}", error=e))

    # --- Korean input bar (§9.6) -----------------------------------------------------------------------------------
    def toggle_hangul(self) -> None:
        if self.hangul_bar.isVisible():
            self.hangul_bar.close_bar()
        else:
            self.open_hangul()

    def open_hangul(self) -> None:
        self.hangul_bar.open_bar()

    def _send_text(self, words: str) -> None:
        if self._device is None:
            self.toast.show_text(t("휴대폰이 준비되면 보낼 수 있습니다"))
            return
        self._device.input.paste_text(words)

    # --- runs ------------------------------------------------------------------------------------------------------------
    def on_run(self, ev: RunEvent) -> None:
        if ev.stage in (RunStage.INSPECTING, RunStage.CHECKING) or ev.info is not None:
            self.app_panel.show_app(ev.info, ev.issues)
        if ev.info is not None:
            self._app_name = ev.info.label
            other_app = self._info is None or ev.info.package != self._info.package
            self._info = ev.info   # a new build of the same app may ask for the microphone now
            if other_app:
                self._app_changed(ev.info)
        if ev.stage in USED:
            self._used_app = True
        if ev.warnings != self._run_warnings:
            self._run_warnings = ev.warnings
            self._sync_banners(self._status.warnings)
        name = self._app_name or t("앱")
        if ev.prompt is not None:
            self._ask(ev)
            return
        words = text(f"run.stage.{ev.stage}")
        was_running = self._run_stage == RunStage.RUNNING   # a RUNNING event again only updates the banners
        self._run_stage = ev.stage
        self._busy_run = ev.stage in BUSY or ev.stage == RunStage.WAITING_ENGINE
        if ev.stage in BUSY and self._status.state == EngineState.READY:
            self.overlay.show_text(f"{name}: {words}")
        elif ev.stage == RunStage.WAITING_ENGINE and self._status.state != EngineState.READY:
            self.overlay.show_text(f"{text(self._status.text_key)}\n{words}", self._status.progress)
        elif ev.stage == RunStage.RUNNING and ev.problem is None and not was_running:
            self.overlay.hide()
            self._show_mic()   # the pipeline applied this app's microphone consent before launching it
            c = self._controls()
            note = ""
            if self._app_uses_mic() and self._host_mic and not (c is not None and c.microphone):
                note = "\n" + t("이 앱은 마이크를 씁니다 — [센서] 탭에서 켤 수 있습니다")
            self.toast.show_text(t("실행 중 · {s:.1f}초", s=ev.elapsed_s) + note, 5000 if note else 3000)
            self.screen_view.setFocus()
        elif not self._busy_run and self._status.state == EngineState.READY:
            self.overlay.hide()
        if not (ev.stage == RunStage.RUNNING and was_running):   # a banner update keeps the launch time shown
            self.status_app.setText(f"{name} {words}" + (" · " + t("{s:.1f}초", s=ev.elapsed_s)
                                                          if ev.stage == RunStage.RUNNING else ""))
        if ev.problem is not None:
            self.show_problem(ev.problem)

    def show_problem(self, err: OrcusError) -> None:
        channel = messages_ko.spec(err.code).channel
        if channel == Channel.MODAL:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Orcus")
            box.setText(text(err.message_key))
            if err.detail:
                box.setDetailedText(f"{err.code}: {err.detail}")
            box.exec()
            return
        self.log_panel.add_problem(err)
        self.tabs.setTabText(self.tabs.indexOf(self.log_panel), t("로그 ({n})", n=self.log_panel.count))
        if channel == Channel.RED_BANNER:
            action = (text(f"remedy.{err.remedy.action}"), lambda _=False, a=err.remedy.action: self.remedy(a)) \
                if err.remedy else None
            first = err.detail.splitlines()[0][:120] if err.detail else ""
            b = Banner(t("{app}가 예기치 않게 종료되었습니다. {detail}", app=self._app_name or t("앱"), detail=first),
                       red=True, action=action)
            self.banners.addWidget(b)
            QTimer.singleShot(15000, b.close)
        elif channel == Channel.BANNER:
            self._sync_banners((*self._status.warnings, err.code))

    def _ask(self, ev: RunEvent) -> None:
        assert ev.prompt is not None
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Orcus")
        box.setText(text(ev.prompt.text_key))
        buttons = {box.addButton(text(f"prompt.choice.{c}"), QMessageBox.ButtonRole.AcceptRole if i == 0
                                 else QMessageBox.ButtonRole.RejectRole): c for i, c in enumerate(ev.prompt.choices)}
        box.exec()
        choice = buttons.get(box.clickedButton(), ev.prompt.choices[-1])   # type: ignore[call-overload]
        self.s.apps.answer(ev.run_id, ev.prompt.prompt_id, choice)

    def _app_action(self, action: str) -> None:
        match action:
            case "grant_all":
                self.grant_all()
            case "clear_data":
                self.s.apps.clear_data()
            case "uninstall":
                if QMessageBox.question(self, t("제거"), t("이 앱을 PC 휴대폰에서 지울까요? 저장된 데이터도 지워집니다.")) \
                        == QMessageBox.StandardButton.Yes:
                    self.s.apps.uninstall()
            case "match_orientation":
                self.match_orientation()
            case "copy_report":
                self.copy_report()
            case "diag":
                self.make_diagnostics()
            case _:
                pass

    def _installed_action(self, action: str, package: str) -> None:
        """The [App] tab's list of apps in the phone."""
        match action:
            case "run":
                self.s.apps.run_installed(package)
            case "remove":
                name = package.rsplit(".", 1)[-1]
                if QMessageBox.question(self, t("지우기"), t("{name}을(를) PC 휴대폰에서 지울까요? 저장된 데이터도 "
                                                           "지워집니다.", name=name)) \
                        == QMessageBox.StandardButton.Yes:
                    self.s.apps.remove_app(package)
            case "refresh":
                self.s.apps.refresh_apps()
            case _:
                pass

    # --- remedies (one handler per RemedyAction) --------------------------------------------------------------------
    def remedy(self, action: RemedyAction) -> None:
        getattr(self, REMEDIES[action])()

    def retry_engine(self) -> None:
        self.engine_panel.hide()
        self.s.engine.retry()

    def run_setup(self, *, first: bool = False, resume: bool = False) -> bool:
        """The setup wizard (§10.5): alone before the window on a PC without the phone's files (`first`), or over
        the window from the engine panel. True once setup got to the end."""
        self._wizard = SetupWizard(self.s.setup, self.logs_dir.parent, self.open_logs, None if first else self,
                                   start=resume)
        try:
            return self._wizard.exec() == QDialog.DialogCode.Accepted
        finally:
            self._wizard = None

    def raise_setup(self) -> None:
        """A second start while the wizard is open brings the wizard forward."""
        if self._wizard is not None:
            self._wizard.raise_()
            self._wizard.activateWindow()

    def open_setup(self) -> None:
        """[Open setup] / [Turn on the Windows Hypervisor Platform]: the wizard does the missing steps (UAC
        included), then the phone starts again. Not while a phone is on: a card in the [Log] tab can be older
        than the phone."""
        if self._status.state not in (EngineState.FAILED, EngineState.NOT_INSTALLED, EngineState.STOPPED):
            self.toast.show_text(t("휴대폰이 이미 켜져 있어 설치 마법사를 열지 않았습니다."))
            return
        if self.run_setup():
            self.engine_panel.hide()
            self.s.engine.retry()

    def copy_it_request(self) -> None:
        QGuiApplication.clipboard().setText(messages_ko.it_request_for(self._status.error, str(self.logs_dir.parent)))
        self.toast.show_text(t("IT 요청문을 복사했습니다"))

    def open_logs(self) -> None:
        # explorer hands over to the shell (§1.2); by full path, never one found in the current folder
        subprocess.Popen([str(win32.system_dir().parent / "explorer.exe"), str(self.logs_dir)])

    def reset_phone(self) -> None:
        if ErrorCode.ENGINE_NO_DISPLAY in self._status.warnings:   # a restart loads the kept golden
            question = t("휴대폰을 다시 켜서 저장된 깨끗한 휴대폰으로 되돌릴까요? 설치한 앱과 데이터가 지워집니다.")
        elif self._status.mode == SessionMode.COLD:
            question = t("느린 그래픽 모드에서는 휴대폰을 처음부터 다시 준비합니다(수 분). 계속할까요?")
        elif self._status.mode == SessionMode.KEEP_STATE:
            question = t("휴대폰을 처음 상태로 되돌릴까요? 설치한 앱과 데이터가 지워지고, 닫을 때는 이 깨끗한 휴대폰을 "
                         "보관합니다.")
        else:
            question = t("휴대폰을 처음 상태로 되돌릴까요? 설치한 앱과 데이터가 지워집니다.")
        if QMessageBox.question(self, t("휴대폰 초기화"), question) == QMessageBox.StandardButton.Yes:
            self.s.engine.reset_device()

    def rerun(self) -> None:
        self.s.apps.rerun()

    def grant_all(self) -> None:
        self.s.apps.grant_all()
        self.toast.show_text(t("요청한 권한을 모두 허용했습니다"))

    def settings_dialog(self, *, gpu: bool = False) -> None:
        dialog = SettingsDialog(self.s, self.logs_dir.parent, self.open_logs, self)
        if gpu:
            focus_gpu(dialog)
        dialog.exec()

    def retry_gpu(self) -> None:
        self.s.engine.retry_gpu()
        self.toast.show_text(t("그래픽 카드 가속을 다시 확인합니다. 휴대폰을 다시 켭니다."))

    def gpu_dialog(self) -> None:
        self.settings_dialog(gpu=True)

    def explain_disk(self) -> None:
        QMessageBox.information(self, t("공간 확보"), t("D: 드라이브에서 필요 없는 파일을 지우세요. 휴지통 비우기와 "
                                                       "다운로드 폴더 정리가 가장 빠릅니다."))

    # --- toolbar actions ---------------------------------------------------------------------------------------------
    def open_apk(self) -> None:
        name = dialogs.open_file(self, t("APK 열기"), str(Path.home()), t("Android 앱 (*.apk);;모든 파일 (*)"))
        if name:
            self.s.open_paths([Path(name)])

    def screenshot(self) -> None:
        if self._device is None:
            return
        self._device.screenshot_png(self.bridge.on_shot)

    def _on_shot(self, png: bytes | None) -> None:
        if png is None:
            self.toast.show_text(t("스크린샷을 받지 못했습니다"))
            return
        name = dialogs.save_file(self, t("스크린샷 저장"), str(Path.home() / "Pictures" / "orcus.png"), "PNG (*.png)")
        if name:
            try:
                Path(name).write_bytes(png)
            except OSError as e:   # the windowed build has no console: say it instead of failing silently
                self.toast.show_text(t("스크린샷을 저장하지 못했습니다: {error}", error=e.strerror or e))
                return
            self.toast.show_text(t("스크린샷을 저장했습니다"))

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.s.report())
        self.toast.show_text(t("보고서를 복사했습니다"))

    def make_diagnostics(self) -> None:
        """A zip of logs and state (names masked) in <home>\\diag, shown in Explorer."""
        self.toast.show_text(t("진단 묶음을 만드는 중입니다…"))

        def work() -> None:
            try:
                self.bridge.on_diag(self.s.make_diagnostics())
            except Exception as e:   # said in the window; the bundle is a help, not a step that must work
                self.bridge.on_diag(e)
        threading.Thread(target=work, name="diag", daemon=True).start()

    def _diag_done(self, result: object) -> None:
        if not isinstance(result, Path):
            QMessageBox.warning(self, t("진단 묶음"), t("진단 묶음을 만들지 못했습니다: {error}", error=result))
            return
        self.toast.show_text(t("진단 묶음을 만들었습니다: {name} (이름·계정은 가렸습니다)", name=result.name), 5000)
        subprocess.Popen([str(win32.system_dir().parent / "explorer.exe"), f"/select,{result}"])

    def help(self) -> None:
        keys = "\n".join(f"{k}: {v}" for k, v in (
            (t("APK 열기"), "Ctrl+O"), (t("다시 실행"), "F5"), (t("뒤로"), t("Esc · 오른쪽 클릭")),
            (t("홈"), t("Ctrl+Shift+H · 가운데 클릭")), (t("최근 앱"), "Ctrl+Shift+R"),
            (t("흔들기"), t("F6 (누르고 있으면 반복)")), (t("왼쪽 / 오른쪽으로 돌리기"), "F7 / F8"),
            (t("한글"), t("폰 화면에서 한/영 키로 바꿔 바로 입력(조합 중인 글자는 화면 아래 말풍선) · 긴 글은 Ctrl+T · 옆 "
                        "[가] 버튼의 입력 바")),
            (t("붙여넣기(한글 포함)"), t("폰 화면에서 Ctrl+V")), (t("스크롤"), t("마우스 휠")),
            (t("두 손가락 확대·축소"), t("Ctrl을 누른 채 끌기 (두 번째 손가락은 화면 가운데를 중심으로 반대편)")),
            (t("사진·소리·동영상"), t("창에 끌어다 놓으면 휴대폰의 Pictures·Music·Movies에 들어갑니다"))))
        QMessageBox.information(self, t("도움말"), t("App Inventor에서 받은 .apk를 끌어다 놓으면 PC 휴대폰에서 바로 "
                                                    "실행합니다. 센서는 오른쪽 [센서] 탭에 있습니다.") + "\n\n" + keys)

    # --- window events -------------------------------------------------------------------------------------------------
    @staticmethod
    def _dropped(event: QDragEnterEvent | QDropEvent) -> tuple[list[Path], bool]:
        """One file (an app), or pictures, sounds and videos, any number; by name only, no I/O (§7.1)."""
        urls = event.mimeData().urls()
        if not urls or not all(u.isLocalFile() for u in urls):
            return [], False
        paths = [Path(u.toLocalFile()) for u in urls]
        if all(media_folder(p) for p in paths):
            return paths, True
        return (paths, False) if len(paths) == 1 else ([], False)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths, into_phone = self._dropped(event)
        if paths:
            event.acceptProposedAction()
            self.overlay.show_text(t("놓으면 휴대폰의 사진·음악·동영상에 넣습니다") if into_phone else t("놓으면 설치합니다"))

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        if not self._busy_run:
            self.overlay.hide()

    def dropEvent(self, event: QDropEvent) -> None:
        paths, _ = self._dropped(event)
        self.overlay.hide()
        if paths:
            event.acceptProposedAction()
            self.s.open_paths(paths)

    def _media_done(self, result: MediaResult) -> None:
        if result.problem is not None or result.phone_path is None:
            if result.problem is not None:
                self.show_problem(result.problem)
            return
        folder = result.phone_path.split("/")[2]
        word, hint = MEDIA_WORDS.get(folder, ("", ""))
        self.toast.show_text(t("휴대폰 {word}에 넣었습니다: {path} ({hint})", word=t(word), path=result.phone_path,
                               hint=t(hint)))

    def event(self, event: QEvent) -> bool:
        # moved to a monitor with other scaling: refit, or the phone is cropped and taps land beside
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            QTimer.singleShot(0, self.fit_phone)
        return super().event(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.screen_view.release()   # §9.3: a held finger never outlives the focus
        elif event.type() == QEvent.Type.WindowStateChange and self._device is not None:
            if self.isMinimized():
                self._device.frames.stop()   # frames only; input and keepalive go on (§9.1)
            else:
                w, h = self._phys()
                self._device.frames.start(w, h, self.pump.on_frame, self.bridge.on_frames_ended)
        super().changeEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.quiet_close and not self._may_close():
            event.ignore()
            return
        self.screen_view.release()
        event.accept()

    def _may_close(self) -> bool:
        """§6.1: a GOLDEN phone forgets this session's app data at close; say so once an app was installed or run.
        The COLD boot that kept the golden (no display) forgets it too, at the next golden load."""
        st = self._status
        forgets = st.mode == SessionMode.GOLDEN or ErrorCode.ENGINE_NO_DISPLAY in st.warnings
        if not (self._used_app and forgets and self.s.settings().ui.confirm_close_data):
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(t("Orcus 닫기"))
        box.setText(t(CLOSE_NOTICE))
        close = box.addButton(t("닫기"), QMessageBox.ButtonRole.AcceptRole)
        keep = None
        if st.mode == SessionMode.GOLDEN:   # §6.1: the no-display COLD boot is not the phone to keep
            keep = box.addButton(t(KEEP_ONCE), QMessageBox.ButtonRole.AcceptRole)
            keep.setToolTip(t("이번 휴대폰을 보관했다가 다음에 열 때 그대로 시작합니다(그다음에 닫을 때 다시 묻습니다)."))
        box.addButton(t("취소"), QMessageBox.ButtonRole.RejectRole)
        never = QCheckBox(t("다시 묻지 않기"))
        box.setCheckBox(never)
        box.exec()
        clicked = box.clickedButton()
        if clicked is not close and (keep is None or clicked is not keep):
            return False
        if keep is not None and clicked is keep:
            self.s.engine.keep_once()   # saved at the normal stop that follows (§6.3)
        if never.isChecked():
            self._save({"ui.confirm_close_data": False})
        return True
