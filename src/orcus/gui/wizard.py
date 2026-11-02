"""Setup wizard (ARCHITECTURE.md §10.5, §11.6): the steps of `orcus-cli setup` as a window.

Welcome and PC check -> license -> Windows Hypervisor Platform -> download -> unpack -> restart (setup goes on by
itself after it) -> acceleration check -> the virtual phone -> done. The phone's first preparation (about 5
minutes) then shows in the main window, from the engine's own status, so setup never builds an engine.

The SetupApi worker does the steps and stops where a person is needed; its events come here through a queued Qt
signal. The license is accepted only by a person: [Agree] turns on once the whole text has been scrolled through,
and nothing here agrees on its own. Closing the wizard cancels setup at the next safe point: setup.json and the
.part files keep what was done, and the next start goes on from there. While dism turns the Windows Hypervisor
Platform on, the wizard does not close: the elevated dism is in this process's KILL_ON_JOB_CLOSE job, so ending
the window would end dism halfway (§1.2).
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QGuiApplication, QSessionManager
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from orcus.common import hostinfo, messages_ko
from orcus.common.i18n import N_, t
from orcus.common.messages_ko import text
from orcus.contracts import SETUP_FREE_GIB, LicenseText, OrcusError, RemedyAction, SetupApi, SetupEvent, SetupStep

GIB = 1 << 30
LISTED = (SetupStep.CHECK, SetupStep.LICENSE, SetupStep.WHPX, SetupStep.DOWNLOAD, SetupStep.EXTRACT,
          SetupStep.REBOOT, SetupStep.ACCEL, SetupStep.AVD, SetupStep.PROVISION)
MARKS = {"pending": "    ", "running": "▶  ", "waiting_user": "…  ", "done": "✓  ", "failed": "!  "}
WORK = {
    SetupStep.CHECK: N_("PC를 점검하고, 설치 폴더를 이 Windows 계정만 쓰도록 잠그는 중입니다…"),
    SetupStep.LICENSE: N_("Google에서 구성요소 목록과 라이선스를 받는 중입니다…"),
    SetupStep.WHPX: N_("Windows 하이퍼바이저 플랫폼을 켜는 중입니다(1–3분). 창을 닫지 마세요."),
    SetupStep.DOWNLOAD: N_("Google에서 Android 에뮬레이터와 휴대폰 이미지를 내려받는 중입니다(약 2.4 GB). 닫아도 다음에 "
                           "이어서 받습니다."),
    SetupStep.EXTRACT: N_("내려받은 파일을 푸는 중입니다. 백신이 새 파일을 검사하느라 느려질 수 있습니다."),
    SetupStep.REBOOT: N_("Windows를 다시 시작해야 하는지 확인하는 중입니다…"),
    SetupStep.ACCEL: N_("가상화 가속(WHPX)을 확인하는 중입니다…"),
    SetupStep.AVD: N_("가상 휴대폰 파일을 만드는 중입니다…"),
}


def _title(words: str) -> QLabel:
    label = QLabel(words)
    f = label.font()
    f.setPointSizeF(f.pointSizeF() * 1.35)
    f.setBold(True)
    label.setFont(f)
    label.setWordWrap(True)
    return label


def _body(words: str = "") -> QLabel:
    label = QLabel(words)
    label.setTextFormat(Qt.TextFormat.PlainText)   # details quote dism, the network or the antivirus
    label.setWordWrap(True)
    return label


def _row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


class _Events(QObject):
    arrived = Signal(object)   # SetupEvent, emitted on the setup worker thread, handled on the GUI thread


class SetupWizard(QDialog):
    def __init__(self, api: SetupApi, home: Path, open_logs: Callable[[], None], parent: QWidget | None = None, *,
                 start: bool = False) -> None:
        super().__init__(parent)
        self._api = api
        self._home = home
        self._open_logs = open_logs
        self._license: LicenseText | None = None
        self._problem: OrcusError | None = None
        self._active = False   # the worker is doing a step or waiting for a person
        self._dism = False     # [Turn on] was pressed and dism has not answered yet
        self._paused = False
        self._quiet = False    # Windows is signing out: close without asking
        self.setWindowTitle(text("setup.title"))
        self.setMinimumSize(780, 560)
        self.steps = QListWidget()
        self.steps.setFixedWidth(260)
        self.steps.setWordWrap(True)
        self.steps.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.steps.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.steps.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._items: dict[SetupStep, QListWidgetItem] = {}
        for step in LISTED:
            item = QListWidgetItem()
            self._items[step] = item
            self.steps.addItem(item)
        self.stack = QStackedWidget()
        self.welcome = self._welcome_page()
        self.work = self._work_page()
        self.license_page = self._license_page()
        self.whpx_page = self._whpx_page()
        self.failed_page = self._failed_page()
        self.reboot_page = self._reboot_page()
        self.done_page = self._done_page()
        for page in (self.welcome, self.work, self.license_page, self.whpx_page, self.failed_page, self.reboot_page,
                     self.done_page):
            self.stack.addWidget(page)
        lay = QHBoxLayout(self)
        lay.addWidget(self.steps)
        lay.addWidget(self.stack, 1)
        for ev in api.events():
            self._mark(ev.step, ev.state)
        self._mark(SetupStep.PROVISION, "pending")
        self._events = _Events(self)
        self._events.arrived.connect(self._on_event)
        self._unsubscribe: Callable[[], None] | None = api.subscribe(self._events.arrived.emit)
        app = QGuiApplication.instance()
        self._app = app if isinstance(app, QGuiApplication) else None
        if self._app is not None:
            self._app.commitDataRequest.connect(self._logoff)
        if start:   # RunOnce after the restart: go on without asking again
            self.begin()

    # --- pages ---------------------------------------------------------------------------------------------------
    def _welcome_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_title(text("setup.title")))
        lay.addWidget(_body(t("App Inventor 앱을 이 PC에서 실행하려면 가상 휴대폰을 한 번 준비해야 합니다.")))
        lay.addWidget(_body(t(
            "• Google에서 Android 에뮬레이터와 휴대폰 이미지를 내려받습니다(약 2.4 GB). 설치 폴더가 있는 드라이브에 "
            "24 GB 이상 비어 있어야 합니다(설치 뒤 약 14 GB).\n"
            "• Windows 하이퍼바이저 플랫폼이 꺼져 있으면 켭니다. 권한(UAC) 창이 한 번 뜨고, Windows를 한 번 다시 "
            "시작합니다.\n"
            "• Google SDK 라이선스를 읽고 직접 동의해야 합니다.\n"
            "• 모두 10–20분쯤 걸립니다. 중간에 닫아도 다음에 열면 이어서 합니다.")))
        lay.addSpacing(8)
        lay.addWidget(_body(t("설치 폴더: {home}", home=self._home)))
        try:
            free = hostinfo.disk_free(self._home) / GIB
        except OSError:
            free = None
        self.free_label = _body(t("빈 공간: {free:.0f} GB", free=free) if free is not None else "")
        if free is not None and free < SETUP_FREE_GIB:
            self.free_label.setStyleSheet("color:#b3261e")
            self.free_label.setText(t("빈 공간이 {free:.0f} GB뿐입니다. {need} GB 이상 비운 뒤 시작하세요.", free=free,
                                      need=SETUP_FREE_GIB))
        lay.addWidget(self.free_label)
        if hostinfo.antivirus().epp_managed:
            lay.addWidget(_body(text("setup.check.v3_epp")))
        lay.addStretch(1)
        self.start_button = QPushButton(t("설치 시작"))
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(lambda _=False: self.begin())
        cancel = QPushButton(t("취소"))
        cancel.clicked.connect(lambda _=False: self.reject())
        lay.addLayout(_row(cancel, self.start_button))
        return page

    def _work_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.work_title = _title("")
        self.work_words = _body()
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.work_detail = _body()
        self.work_detail.setStyleSheet("color:#52606d")
        lay.addWidget(self.work_title)
        lay.addWidget(self.work_words)
        lay.addSpacing(8)
        lay.addWidget(self.bar)
        lay.addWidget(self.work_detail)
        lay.addStretch(1)
        self.pause_button = QPushButton(t("일시정지"))
        self.pause_button.clicked.connect(lambda _=False: self._toggle_pause())
        self.pause_button.hide()
        stop = QPushButton(t("멈추기"))
        stop.clicked.connect(lambda _=False: self.reject())
        lay.addLayout(_row(stop, self.pause_button))
        return page

    def _license_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_title(text("setup.step.license")))
        lay.addWidget(_body(t("Orcus는 Google의 Android 에뮬레이터와 휴대폰 이미지를 이 PC에 내려받아 씁니다(다른 곳에 "
                              "나눠 주지 않습니다). 내려받기 전에 아래 Google 라이선스를 읽고 직접 동의해야 합니다.")))
        self.license_changed = _body()
        self.license_changed.setStyleSheet("color:#b3261e")
        self.license_summary = _body()
        # a light box needs its own text color: a dark Windows theme draws light text
        self.license_summary.setStyleSheet("background:#eef2f7;color:#1f2933;border-radius:6px;padding:8px")
        self.license_packages = _body()
        self.license_packages.setStyleSheet("color:#52606d")
        self.license_text = QPlainTextEdit()
        self.license_text.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.license_text.setFont(mono)
        bar = self.license_text.verticalScrollBar()
        bar.valueChanged.connect(lambda _v: self._read_to_end())
        bar.rangeChanged.connect(lambda _a, _b: self._read_to_end())
        self.license_hint = _body()
        self.license_hint.setStyleSheet("color:#52606d")
        for w in (self.license_changed, self.license_summary, self.license_packages):
            lay.addWidget(w)
        lay.addWidget(self.license_text, 1)
        lay.addWidget(self.license_hint)
        decline = QPushButton(t("동의하지 않음"))
        decline.clicked.connect(lambda _=False: self._decline())
        self.agree = QPushButton(t("동의합니다"))
        self.agree.clicked.connect(lambda _=False: self._agree())
        lay.addLayout(_row(decline, self.agree))
        return page

    def _whpx_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_title(text("setup.step.whpx")))
        lay.addWidget(_body(t("가상 휴대폰은 Windows의 'Windows 하이퍼바이저 플랫폼' 기능 위에서 돕니다. 이 PC는 아직 "
                              "꺼져 있습니다. [켜기]를 누르면 Windows가 권한(UAC) 창을 띄웁니다. [예]를 누르세요. 켠 "
                              "뒤에는 Windows를 한 번 다시 시작해야 합니다(내려받기는 그 전에 끝납니다).")))
        self.whpx_hint = _body()
        lay.addWidget(self.whpx_hint)
        self.whpx_request = QPushButton(text("remedy.copy_it_request"))
        self.whpx_request.clicked.connect(lambda _=False: self._copy_request(None))
        lay.addWidget(self.whpx_request, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        later = QPushButton(t("나중에"))
        later.clicked.connect(lambda _=False: self.reject())
        self.whpx_button = QPushButton(t("켜기"))
        self.whpx_button.clicked.connect(lambda _=False: self._enable_whpx())
        lay.addLayout(_row(later, self.whpx_button))
        return page

    def _failed_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.failed_title = _title("")
        self.failed_words = _body()
        self.failed_detail = _body()
        self.failed_detail.setStyleSheet("color:#52606d")
        self.failed_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for w in (self.failed_title, self.failed_words, self.failed_detail):
            lay.addWidget(w)
        extra = QHBoxLayout()
        self.request_button = QPushButton(text("remedy.copy_it_request"))
        self.request_button.clicked.connect(lambda _=False: self._copy_request(self._problem))
        logs = QPushButton(t("로그 폴더 열기"))
        logs.clicked.connect(lambda _=False: self._open_logs())
        extra.addWidget(self.request_button)
        extra.addWidget(logs)
        extra.addStretch(1)
        lay.addLayout(extra)
        lay.addStretch(1)
        close = QPushButton(t("닫기"))
        close.clicked.connect(lambda _=False: self.reject())
        self.anyway_button = QPushButton(t("그래도 내려받기"))
        self.anyway_button.setToolTip(t("Windows 하이퍼바이저 플랫폼 없이 내려받기만 계속합니다. 휴대폰을 켜려면 나중에 "
                                        "켜야 합니다."))
        self.anyway_button.clicked.connect(lambda _=False: self._anyway())
        self.retry_button = QPushButton(t("다시 시도"))
        self.retry_button.clicked.connect(lambda _=False: self._retry())
        lay.addLayout(_row(close, self.anyway_button, self.retry_button))
        return page

    def _reboot_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_title(t("Windows를 다시 시작하세요")))
        lay.addWidget(_body(text("setup.reboot.needed")))
        lay.addWidget(_body(t("다시 시작하고 로그인하면 이 창이 다시 열려 남은 단계를 마칩니다.")))
        lay.addStretch(1)
        close = QPushButton(t("닫기"))
        close.clicked.connect(lambda _=False: self.reject())
        lay.addLayout(_row(close))
        return page

    def _done_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(_title(t("설치가 끝났습니다")))
        lay.addWidget(_body(t("이제 가상 휴대폰을 처음 준비합니다. 약 5분 걸리고 한 번만 합니다. 준비하는 동안 Orcus 창을 "
                              "닫지 마세요.")))
        lay.addStretch(1)
        self.done_button = QPushButton(t("시작"))
        self.done_button.setDefault(True)
        self.done_button.clicked.connect(lambda _=False: self._finish())
        lay.addLayout(_row(self.done_button))
        return page

    # --- the worker's events ---------------------------------------------------------------------------------------
    def begin(self) -> None:
        """[Start setup]: the worker goes through the steps; done ones pass at once."""
        self._active = True
        self._show_work(SetupStep.CHECK, None)
        self._api.run()

    def _on_event(self, ev: SetupEvent) -> None:
        self._mark(ev.step, ev.state)
        if not (ev.step is SetupStep.WHPX and ev.state == "running"):
            self._dism = False
        if ev.state == "failed":
            self._active = ev.can_continue   # the worker still waits for [Download anyway]
            self._show_failed(ev)
        elif ev.state == "waiting_user":
            if ev.step is SetupStep.LICENSE:
                self._show_license(ev)
            elif ev.step is SetupStep.WHPX:
                self._show_whpx()
            elif ev.step is SetupStep.REBOOT:
                self._active = False
                self.stack.setCurrentWidget(self.reboot_page)
        elif ev.step is SetupStep.DONE and ev.state == "done":
            self._active = False
            self._mark(SetupStep.PROVISION, "running")
            self.stack.setCurrentWidget(self.done_page)
        elif ev.state == "running":
            self._show_work(ev.step, ev)

    def _mark(self, step: SetupStep, state: str) -> None:
        item = self._items.get(step)
        if item is None:
            return
        name = text(f"setup.step.{step}")
        if step is SetupStep.PROVISION:
            name = t("{step} (설치 뒤 창에서)", step=name)
        item.setText(MARKS.get(state, "    ") + name)
        f = item.font()
        f.setBold(state in ("running", "waiting_user", "failed"))
        item.setFont(f)

    def _show_work(self, step: SetupStep, ev: SetupEvent | None) -> None:
        self.work_title.setText(text(f"setup.step.{step}"))
        self.work_words.setText(t(WORK[step]) if step in WORK else "")
        self.pause_button.setVisible(step is SetupStep.DOWNLOAD)
        if ev is not None and ev.progress is not None:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(ev.progress * 1000))
        else:
            self.bar.setRange(0, 0)   # busy
        detail = ""
        if ev is not None and ev.bytes_total:
            detail = f"{ev.bytes_done / 1e6:,.0f} MB / {ev.bytes_total / 1e6:,.0f} MB"
            if ev.rate_bps:
                detail += f" · {ev.rate_bps / 1e6:.1f} MB/s"
            if ev.eta_s:
                detail += " · " + t("약 {minutes}분 {seconds}초 남음", minutes=int(ev.eta_s // 60),
                                    seconds=int(ev.eta_s % 60))
        if self._paused and step is SetupStep.DOWNLOAD:
            detail = t("일시정지했습니다. [계속]을 누르면 이어서 받습니다.")
        self.work_detail.setText(detail)
        self.stack.setCurrentWidget(self.work)

    def _show_license(self, ev: SetupEvent) -> None:
        lt = self._api.license()
        if lt is None:
            return
        self._license = lt
        self.license_changed.setText(text(ev.problem.message_key) if ev.problem is not None else "")
        self.license_changed.setVisible(ev.problem is not None)
        self.license_summary.setText(text("setup.license.summary"))
        self.license_packages.setText(text("setup.license.packages", packages=", ".join(lt.packages)))
        self.license_text.setPlainText(lt.text)
        self.license_text.verticalScrollBar().setValue(0)
        self.license_hint.setText(t("끝까지 내려 읽으면 [동의합니다]가 켜집니다."))
        self.agree.setEnabled(False)
        self.stack.setCurrentWidget(self.license_page)
        QTimer.singleShot(0, self._read_to_end)   # after the layout: a text that fits has nothing to scroll

    def _show_whpx(self) -> None:
        elevation = hostinfo.elevation()
        if elevation == "default":
            hint = text("setup.check.admin_default")
        elif elevation == "limited":
            hint = text("setup.check.admin_limited")
        else:
            hint = ""
        self.whpx_hint.setText(hint)
        self.whpx_hint.setVisible(bool(hint))
        self.whpx_request.setVisible(elevation == "default")
        self.whpx_button.setEnabled(True)
        self.stack.setCurrentWidget(self.whpx_page)

    def _show_failed(self, ev: SetupEvent) -> None:
        err = ev.problem
        self._problem = err
        self.failed_title.setText(t("{step}에서 멈췄습니다", step=text(f"setup.step.{ev.step}")))
        self.failed_words.setText(text(err.message_key) if err is not None else "")
        self.failed_detail.setText(f"{err.code}: {err.detail}" if err is not None and err.detail else "")
        self.request_button.setVisible(err is not None and err.remedy is not None
                                       and err.remedy.action is RemedyAction.COPY_IT_REQUEST)
        self.anyway_button.setVisible(ev.can_continue)
        self.stack.setCurrentWidget(self.failed_page)

    # --- a person's choices ----------------------------------------------------------------------------------------
    def _read_to_end(self) -> None:
        bar = self.license_text.verticalScrollBar()
        if self._license is not None and bar.value() >= bar.maximum():
            self.agree.setEnabled(True)
            self.license_hint.setText(t("다 읽었으면 [동의합니다]를 누르세요."))

    def _agree(self) -> None:
        lt = self._license
        if lt is None or not self.agree.isEnabled():
            return
        self.agree.setEnabled(False)
        self._license = None
        self._api.accept_license(lt.sha256, messages_ko.current_user())
        self._show_work(SetupStep.LICENSE, None)

    def _decline(self) -> None:
        QMessageBox.information(self, text("setup.title"), t("동의하지 않으면 설치할 수 없습니다. 언제든 다시 열 수 "
                                                             "있습니다."))
        self._stop()
        super().reject()

    def _enable_whpx(self) -> None:
        self.whpx_button.setEnabled(False)
        self._dism = True
        self._api.enable_whpx()
        self._show_work(SetupStep.WHPX, None)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._api.pause()
        else:
            self._api.resume()
        self.pause_button.setText(t("계속") if self._paused else t("일시정지"))
        if self._paused:
            self.work_detail.setText(t("일시정지했습니다. [계속]을 누르면 이어서 받습니다."))

    def _anyway(self) -> None:
        self._active = True
        self._api.resume()
        self._show_work(SetupStep.DOWNLOAD, None)

    def _retry(self) -> None:
        self._api.cancel()   # a worker still waiting on the failure ends, then the new run starts
        self.begin()

    def _copy_request(self, err: OrcusError | None) -> None:
        request = messages_ko.it_request_for(err, str(self._home), t("아직 시도 전"))
        QGuiApplication.clipboard().setText(request)
        QMessageBox.information(self, text("remedy.copy_it_request"),
                                t("IT 요청문을 복사했습니다. 메일이나 메신저에 붙여 넣어 관리자에게 보내세요."))

    # --- the end -----------------------------------------------------------------------------------------------------
    def _disconnect(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._app is not None:
            try:
                self._app.commitDataRequest.disconnect(self._logoff)
            except (RuntimeError, TypeError):
                pass
            self._app = None

    def _stop(self) -> None:
        self._disconnect()
        self._api.cancel()
        self._active = False

    def _finish(self) -> None:
        self._disconnect()
        self.accept()

    def _logoff(self, _manager: QSessionManager) -> None:
        self._quiet = True
        self.reject()

    def reject(self) -> None:
        """Esc, the close box, [Cancel], [Later], [Stop]: setup stops at the next safe point and goes on next time.
        On the last page it is the same as [Start]."""
        if not self._quiet:
            if self.stack.currentWidget() is self.done_page:
                self._finish()
                return
            if self._dism:
                QMessageBox.information(self, t("설치 멈추기"), t("Windows 하이퍼바이저 플랫폼을 켜는 동안에는 멈출 수 "
                                                               "없습니다(1–3분). 끝나면 닫을 수 있습니다."))
                return
            if self._active and QMessageBox.question(
                    self, t("설치 멈추기"), t("설치를 멈출까요? 다음에 Orcus를 열면 멈춘 곳부터 이어서 합니다.")) \
                    != QMessageBox.StandardButton.Yes:
                return
        self._stop()
        super().reject()
