"""Main window (ARCHITECTURE.md §10.1): toolbar, the phone with its overlays, [App]/[Log] panels, status bar.

Everything arrives through the Bridge on the main thread. The window only calls SessionApi, whose methods all
return at once. Feedback goes to one channel per severity (§10.2, the code -> channel table in messages_ko).
"""

import subprocess
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDragLeaveEvent, QDropEvent, QGuiApplication, \
    QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
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
from orcus.common.messages_ko import Channel, text
from orcus.contracts import (
    DeviceSession,
    EngineState,
    EngineStatus,
    EpochCause,
    ErrorCode,
    HwKey,
    OrcusError,
    RemedyAction,
    Rotation,
    RunEvent,
    RunStage,
    SessionApi,
    SessionMode,
)

from . import dialogs
from .bridge import Bridge
from .frame_pump import FramePump
from .gpu_dialog import GpuDialog
from .keymap import SHORTCUTS
from .overlays import Banner, EnginePanel, Overlay, Toast
from .panels import AppPanel, LogPanel
from .phone import PhoneView
from .screen import ScreenView, fit

PANEL_W = 360
WINDOW_CHROME_H = 192   # title bar, toolbar, banner room, status bar (§9.1: ~256 px with the bezel and nav row)
BUSY = (RunStage.STAGING, RunStage.INSPECTING, RunStage.CHECKING, RunStage.INSTALLING, RunStage.LAUNCHING)
LANDSCAPES = (Rotation.LANDSCAPE, Rotation.REVERSE_LANDSCAPE)
CRASH_NOTICE = {SessionMode.GOLDEN: "휴대폰이 멈춰서 다시 켰습니다. 저장된 앱 데이터는 처음 상태로 돌아갔습니다.",
                SessionMode.COLD: "휴대폰이 멈춰서 다시 켰습니다.",
                SessionMode.KEEP_STATE: "휴대폰이 멈춰서 마지막으로 저장한 상태로 다시 켰습니다."}
NO_DISPLAY_CHIP = "이번만 느린 시작 — 초기화하면 깨끗한 휴대폰으로"   # COLD boot that kept the golden (3a finding 18)
MODE_CHIP = {SessionMode.GOLDEN: "깨끗한 휴대폰 모드 — 닫으면 처음 상태로", SessionMode.COLD: "느린 그래픽 모드 · 휴대폰 상태가 유지됨",
             SessionMode.KEEP_STATE: "휴대폰 유지 모드"}
# RemedyAction -> handler method name; the unit test checks every action has one (§3)
REMEDIES: dict[RemedyAction, str] = {
    RemedyAction.RETRY: "retry_engine", RemedyAction.ENABLE_WHPX: "explain_whpx",
    RemedyAction.COPY_IT_REQUEST: "copy_it_request", RemedyAction.REPAIR_SETUP: "explain_setup",
    RemedyAction.OPEN_LOG: "open_logs", RemedyAction.RESET_PHONE: "reset_phone", RemedyAction.RUN_AGAIN: "rerun",
    RemedyAction.GRANT_ALL: "grant_all", RemedyAction.CHANGE_GPU_MODE: "gpu_dialog",
    RemedyAction.FREE_DISK: "explain_disk", RemedyAction.CONTINUE_ANYWAY: "rerun",
}


class MainWindow(QMainWindow):
    def __init__(self, session: SessionApi, bridge: Bridge, logs_dir: Path) -> None:
        super().__init__()
        self.s = session
        self.bridge = bridge
        self.logs_dir = logs_dir
        self.setWindowTitle("Orcus APK Emulator")
        self.setAcceptDrops(True)
        self.pump = FramePump(bridge)
        self.screen_view = ScreenView(self.pump, lambda: self.s.settings().input)
        self.phone = PhoneView(self.screen_view, self._phone_key)
        self.overlay = Overlay(self.screen_view)
        self.overlay.clicked.connect(self._overlay_clicked)
        self.toast = Toast(self.screen_view)
        self.engine_panel = EnginePanel(self.screen_view)
        self.engine_panel.remedy.connect(self.remedy)
        self.engine_panel.open_log.connect(self.open_logs)
        self.engine_panel.copy_report.connect(self.copy_report)
        self.banners = QVBoxLayout()
        self.banner_widgets: dict[str, Banner] = {}
        self.app_panel = AppPanel()
        self.app_panel.action.connect(self._app_action)
        self.log_panel = LogPanel()
        self.log_panel.remedy.connect(lambda err: self.remedy(err.remedy.action) if err.remedy else None)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.app_panel, "앱")
        self.tabs.addTab(self.log_panel, "로그")
        self.tabs.setFixedWidth(PANEL_W)
        left = QVBoxLayout()
        left.addLayout(self.banners)
        left.addWidget(self.phone, 0, Qt.AlignmentFlag.AlignHCenter)
        left.addStretch(1)
        row = QHBoxLayout()
        row.addLayout(left, 1)
        row.addWidget(self.tabs)
        central = QWidget()
        central.setLayout(row)
        self.setCentralWidget(central)
        self.status_engine = QLabel()
        self.status_app = QLabel()   # carries the app's own label: never rich text (3a review)
        for label in (self.status_engine, self.status_app):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_mic = QLabel("마이크 꺼짐")
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
        bridge.engine.connect(self.on_engine)
        bridge.run.connect(self.on_run)
        bridge.log.connect(self.log_panel.append_log)
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
        bar = QToolBar("도구")
        bar.setMovable(False)
        self.addToolBar(bar)
        for label, slot, tip in (("APK 열기", self.open_apk, "Ctrl+O"), ("다시 실행", self.rerun, "F5"),
                                 ("앱 중지", self.s.apps.stop_app, ""), ("데이터 지우고 실행", self.s.apps.clear_data, ""),
                                 ("휴대폰 초기화", self.reset_phone, "확인 후 초기화"), ("스크린샷", self.screenshot, ""),
                                 ("설정", self.gpu_dialog, "GPU 모드"), ("도움말", self.help, "")):
            act = QAction(label, self)
            act.setToolTip(f"{label} ({tip})" if tip else label)
            act.triggered.connect(slot)
            bar.addAction(act)

    def _shortcuts(self) -> None:
        actions = {"open_apk": self.open_apk, "rerun": self.rerun,
                   "home": lambda: self._phone_key(HwKey.HOME, False),
                   "recents": lambda: self._phone_key(HwKey.RECENTS, False),
                   "overlay": self._toggle_overlay}
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
        chip = NO_DISPLAY_CHIP if ErrorCode.ENGINE_NO_DISPLAY in st.warnings else MODE_CHIP[st.mode]
        parts = [text(st.text_key), android, gpu, chip if st.state == EngineState.READY else ""]
        self.status_engine.setText("● " + " · ".join(p for p in parts if p))
        if st.state == EngineState.READY:
            self.engine_panel.hide()
            if st.epoch != self._epoch:
                self._epoch = st.epoch
                self._attach_device()
                if st.epoch_cause == EpochCause.CRASH_RESTART:
                    self.toast.show_text(CRASH_NOTICE[st.mode], 6000)
                elif st.epoch_cause in (EpochCause.RESET, EpochCause.RESET_RECOVERY):
                    self.toast.show_text("휴대폰 초기화 완료")
            if not self._busy_run:
                self.overlay.hide()
            if not st.display_on:
                self.overlay.show_text("클릭하면 화면이 켜집니다")
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
        wanted = {str(c): c for c in warnings}
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
                # clicked(bool) fills the first parameter: `lambda a=act:` got False and the button did nothing (3a)
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

    def _detach_device(self) -> None:
        if self._device is None:
            return
        self.screen_view.release()
        self.screen_view.set_input(None)
        self._device.frames.stop()
        self._device = None
        self.pump.clear()
        self.screen_view.clear()

    def _on_frame(self) -> None:
        turned = self.screen_view.show_frame()
        if turned is not None and (turned in LANDSCAPES) != self._landscape:
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

    def _overlay_clicked(self) -> None:
        if self._status.state == EngineState.READY and not self._status.display_on:
            self._phone_key(HwKey.POWER, False)
            self.overlay.hide()

    # --- runs ------------------------------------------------------------------------------------------------------------
    def on_run(self, ev: RunEvent) -> None:
        if ev.stage in (RunStage.INSPECTING, RunStage.CHECKING) or ev.info is not None:
            self.app_panel.show_app(ev.info, ev.issues)
        if ev.info is not None:
            self._app_name = ev.info.label
        name = self._app_name or "앱"
        if ev.prompt is not None:
            self._ask(ev)
            return
        words = text(f"run.stage.{ev.stage}")
        self._busy_run = ev.stage in BUSY or ev.stage == RunStage.WAITING_ENGINE
        if ev.stage in BUSY and self._status.state == EngineState.READY:
            self.overlay.show_text(f"{name}: {words}")
        elif ev.stage == RunStage.WAITING_ENGINE and self._status.state != EngineState.READY:
            self.overlay.show_text(f"{text(self._status.text_key)}\n{words}", self._status.progress)
        elif ev.stage == RunStage.RUNNING and ev.problem is None:
            self.overlay.hide()
            self.toast.show_text(f"실행 중 · {ev.elapsed_s:.1f}초")
            self.screen_view.setFocus()
        elif not self._busy_run and self._status.state == EngineState.READY:
            self.overlay.hide()
        self.status_app.setText(f"{name} {words}" + (f" · {ev.elapsed_s:.1f}초" if ev.stage == RunStage.RUNNING
                                                      else ""))
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
        self.tabs.setTabText(1, f"로그 ({self.log_panel.count})")
        if channel == Channel.RED_BANNER:
            action = (text(f"remedy.{err.remedy.action}"), lambda _=False, a=err.remedy.action: self.remedy(a)) \
                if err.remedy else None
            first = err.detail.splitlines()[0][:120] if err.detail else ""
            b = Banner(f"{self._app_name or '앱'}가 예기치 않게 종료되었습니다. {first}", red=True, action=action)
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
                if QMessageBox.question(self, "제거", "이 앱을 PC 휴대폰에서 지울까요? 저장된 데이터도 지워집니다.") \
                        == QMessageBox.StandardButton.Yes:
                    self.s.apps.uninstall()
            case "match_orientation":
                self.s.apps.match_orientation()
            case "copy_report":
                self.copy_report()
            case _:
                pass

    # --- remedies (one handler per RemedyAction) --------------------------------------------------------------------
    def remedy(self, action: RemedyAction) -> None:
        getattr(self, REMEDIES[action])()

    def retry_engine(self) -> None:
        self.engine_panel.hide()
        self.s.engine.retry()

    def explain_whpx(self) -> None:
        QMessageBox.information(self, "Windows 하이퍼바이저 플랫폼",
                                "Windows 하이퍼바이저 플랫폼을 켜야 휴대폰을 켤 수 있습니다. 터미널에서 "
                                "orcus-cli setup을 실행하면 켤 수 있고(UAC 창에서 [예]), 학교 PC라면 IT 요청문을 "
                                "관리자에게 보내세요.")
        self.copy_it_request()

    def copy_it_request(self) -> None:
        detail = self._status.error.detail if self._status.error else ""
        QGuiApplication.clipboard().setText(messages_ko.it_request(messages_ko.IT_REQUEST_WHPX, detail))
        self.toast.show_text("IT 요청문을 복사했습니다")

    def explain_setup(self) -> None:
        QMessageBox.information(self, "설치 복구", "터미널에서 orcus-cli setup을 실행하면 빠진 파일을 다시 받습니다.")

    def open_logs(self) -> None:
        # explorer hands over to the shell (§1.2); by full path, never one found in the current folder (3a review)
        subprocess.Popen([str(win32.system_dir().parent / "explorer.exe"), str(self.logs_dir)])

    def reset_phone(self) -> None:
        if ErrorCode.ENGINE_NO_DISPLAY in self._status.warnings:   # a restart loads the kept golden
            question = "휴대폰을 다시 켜서 저장된 깨끗한 휴대폰으로 되돌릴까요? 설치한 앱과 데이터가 지워집니다."
        elif self._status.mode == SessionMode.COLD:
            question = "느린 그래픽 모드에서는 휴대폰을 처음부터 다시 준비합니다(수 분). 계속할까요?"
        else:
            question = "휴대폰을 처음 상태로 되돌릴까요? 설치한 앱과 데이터가 지워집니다."
        if QMessageBox.question(self, "휴대폰 초기화", question) == QMessageBox.StandardButton.Yes:
            self.s.engine.reset_device()

    def rerun(self) -> None:
        self.s.apps.rerun()

    def grant_all(self) -> None:
        self.s.apps.grant_all()
        self.toast.show_text("요청한 권한을 모두 허용했습니다")

    def gpu_dialog(self) -> None:
        GpuDialog(self.s, self).exec()

    def explain_disk(self) -> None:
        QMessageBox.information(self, "공간 확보", "D: 드라이브에서 필요 없는 파일을 지우세요. 휴지통 비우기와 "
                                              "다운로드 폴더 정리가 가장 빠릅니다.")

    # --- toolbar actions ---------------------------------------------------------------------------------------------
    def open_apk(self) -> None:
        name = dialogs.open_file(self, "APK 열기", str(Path.home()), "Android 앱 (*.apk);;모든 파일 (*)")
        if name:
            self.s.open_paths([Path(name)])

    def screenshot(self) -> None:
        if self._device is None:
            return
        self._device.screenshot_png(self.bridge.on_shot)

    def _on_shot(self, png: bytes | None) -> None:
        if png is None:
            self.toast.show_text("스크린샷을 받지 못했습니다")
            return
        name = dialogs.save_file(self, "스크린샷 저장", str(Path.home() / "Pictures" / "orcus.png"), "PNG (*.png)")
        if name:
            try:
                Path(name).write_bytes(png)
            except OSError as e:   # the windowed build has no console: say it instead of failing silently
                self.toast.show_text(f"스크린샷을 저장하지 못했습니다: {e.strerror or e}")
                return
            self.toast.show_text("스크린샷을 저장했습니다")

    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.s.report())
        self.toast.show_text("보고서를 복사했습니다")

    def help(self) -> None:
        keys = "\n".join(f"{k}: {v}" for k, v in (("APK 열기", "Ctrl+O"), ("다시 실행", "F5"), ("뒤로", "Esc · 오른쪽 클릭"),
                                                  ("홈", "Ctrl+Shift+H · 가운데 클릭"), ("최근 앱", "Ctrl+Shift+R"),
                                                  ("붙여넣기(한글 포함)", "폰 화면에서 Ctrl+V")))
        QMessageBox.information(self, "도움말", "App Inventor에서 받은 .apk를 끌어다 놓으면 PC 휴대폰에서 바로 "
                                             "실행합니다.\n\n" + keys)

    # --- window events -------------------------------------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():   # no I/O while dragging (§7.1)
            event.acceptProposedAction()
            self.overlay.show_text("놓으면 설치합니다")

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        if not self._busy_run:
            self.overlay.hide()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        self.overlay.hide()
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.s.open_paths([Path(urls[0].toLocalFile())])

    def event(self, event: QEvent) -> bool:
        # moved to a monitor with other scaling: refit, or the phone is cropped and taps land beside (3a review)
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
        self.screen_view.release()
        event.accept()
