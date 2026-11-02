"""Setup wizard without a PC change (offscreen Qt, a fake SetupApi): the pages follow the worker's events, the
license is agreed to only after the whole text was scrolled through and only by a click, UAC, pause, a failure with
its IT request and [Retry], the restart, the end; closing stops setup. Also the window's [Open setup] and the
RunOnce command the wizard leaves for after a restart."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from orcus.app import compose
from orcus.common import i18n, messages_ko
from orcus.common.paths import OrcusPaths
from orcus.contracts import EngineState, ErrorCode, LicenseText, RemedyAction, SetupEvent, SetupStep
from orcus.gui import wizard, window
from orcus.gui.bridge import Bridge
from test_gui import FakeSessionApi, qapp   # noqa: F401  (qapp is a fixture)

LICENSE = LicenseText("android-sdk-license", "\n".join(f"{i}. Terms and Conditions" for i in range(400)), "sha-1",
                      ("platform-tools", "emulator"))


class FakeSetup:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.cb: Callable[[SetupEvent], None] | None = None
        self.pending: LicenseText | None = None

    def events(self) -> tuple[SetupEvent, ...]:
        return tuple(ev(s, "pending") for s in SetupStep)

    def run(self) -> None:
        self.calls.append(("run",))

    def license(self) -> LicenseText | None:
        return self.pending

    def accept_license(self, sha256: str, accepted_by: str) -> None:
        self.calls.append(("accept", sha256, accepted_by))

    def enable_whpx(self) -> None:
        self.calls.append(("whpx",))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    def cancel(self) -> None:
        self.calls.append(("cancel",))

    def subscribe(self, cb: Callable[[SetupEvent], None]) -> Callable[[], None]:
        self.cb = cb

        def unsubscribe() -> None:
            self.cb = None
        return unsubscribe

    def emit(self, event: SetupEvent, app: QApplication) -> None:
        assert self.cb is not None
        self.cb(event)
        app.processEvents()


def ev(step: SetupStep, state: str, *, progress: float | None = None, done: int = 0, total: int = 0,
       rate: float = 0.0, eta: float | None = None, problem: object = None, can_continue: bool = False) -> SetupEvent:
    return SetupEvent(step, state, progress, done, total, rate, eta, problem, can_continue)   # type: ignore[arg-type]


@pytest.fixture
def made(qapp: QApplication, tmp_path: Path) -> Iterator[tuple[wizard.SetupWizard, FakeSetup]]:
    api = FakeSetup()
    w = wizard.SetupWizard(api, tmp_path, lambda: None)
    w.show()
    yield w, api
    w.hide()


def test_the_pages_follow_setup_and_the_license_needs_a_person(qapp: QApplication,
                                                              made: tuple[wizard.SetupWizard, FakeSetup]) -> None:
    w, api = made
    assert w.stack.currentWidget() is w.welcome and api.calls == []   # nothing starts without [Start setup]
    w.start_button.click()
    assert api.calls == [("run",)] and w.stack.currentWidget() is w.work
    api.emit(ev(SetupStep.CHECK, "running"), qapp)
    assert w.work_title.text() == messages_ko.text("setup.step.check")
    api.pending = LICENSE
    api.emit(ev(SetupStep.LICENSE, "waiting_user"), qapp)
    assert w.stack.currentWidget() is w.license_page
    assert "Terms and Conditions" in w.license_text.toPlainText() and "emulator" in w.license_packages.text()
    assert "참고용 요약" in w.license_summary.text()
    w.agree.click()
    assert not w.agree.isEnabled() and api.calls == [("run",)]   # not read to the end: no agreement
    bar = w.license_text.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 2)
    assert not w.agree.isEnabled()
    bar.setValue(bar.maximum())
    assert w.agree.isEnabled() and "다 읽었으면" in w.license_hint.text()
    w.agree.click()
    assert api.calls[-1] == ("accept", "sha-1", messages_ko.current_user())
    assert w.stack.currentWidget() is w.work
    api.pending = replace(LICENSE, sha256="sha-2")   # a second license: read again from the top
    api.emit(ev(SetupStep.LICENSE, "waiting_user"), qapp)
    assert not w.agree.isEnabled() and "끝까지" in w.license_hint.text()
    api.emit(ev(SetupStep.LICENSE, "done"), qapp)
    assert w.steps.item(1).text().startswith("✓")


def test_whpx_pause_and_progress(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    w, api = made
    w.begin()
    api.emit(ev(SetupStep.WHPX, "waiting_user"), qapp)
    assert w.stack.currentWidget() is w.whpx_page
    w.whpx_button.click()
    assert api.calls[-1] == ("whpx",) and w.stack.currentWidget() is w.work
    told: list[object] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a))
    w.reject()   # dism runs in this process's job: closing now would end it halfway (§1.2)
    assert w.isVisible() and told and ("cancel",) not in api.calls
    api.emit(ev(SetupStep.WHPX, "running"), qapp)
    w.reject()
    assert w.isVisible() and len(told) == 2
    api.emit(ev(SetupStep.WHPX, "done"), qapp)   # dism answered: closing asks as usual again
    api.emit(ev(SetupStep.DOWNLOAD, "running", progress=0.25, done=600_000_000, total=2_400_000_000, rate=5e6,
                eta=360.0), qapp)
    assert w.bar.value() == 250 and not w.pause_button.isHidden()
    assert "600 MB / 2,400 MB" in w.work_detail.text() and "5.0 MB/s" in w.work_detail.text()
    w.pause_button.click()
    assert api.calls[-1] == ("pause",) and w.pause_button.text() == "계속"
    w.pause_button.click()
    assert api.calls[-1] == ("resume",) and w.pause_button.text() == "일시정지"
    api.emit(ev(SetupStep.EXTRACT, "running"), qapp)
    assert w.pause_button.isHidden() and w.bar.maximum() == 0   # no progress yet: busy


def test_a_failure_shows_its_request_and_can_go_on(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    w, api = made
    w.begin()
    denied = messages_ko.error(ErrorCode.SETUP_UAC_DENIED, "launched=False last_error=1223")
    api.emit(ev(SetupStep.WHPX, "failed", problem=denied, can_continue=True), qapp)
    assert w.stack.currentWidget() is w.failed_page
    assert not w.request_button.isHidden() and not w.anyway_button.isHidden()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w.request_button.click()
    copied = QApplication.clipboard().text()
    assert "HypervisorPlatform" in copied and "last_error=1223" in copied
    w.anyway_button.click()
    assert api.calls[-1] == ("resume",) and w.stack.currentWidget() is w.work
    lost = messages_ko.error(ErrorCode.SETUP_NETWORK, "timed out")
    api.emit(ev(SetupStep.DOWNLOAD, "failed", problem=lost), qapp)
    assert w.request_button.isHidden() and w.anyway_button.isHidden()
    assert messages_ko.text("error.setup_network") in w.failed_words.text()
    w.retry_button.click()
    assert api.calls[-2:] == [("cancel",), ("run",)]


def test_a_whpx_failure_the_worker_does_not_wait_on_offers_no_download(
        qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup]) -> None:
    """The feature was undone after a restart: the worker has ended, so [Download anyway] would wait for nothing."""
    w, api = made
    w.begin()
    reverted = messages_ko.error(ErrorCode.SETUP_WHPX_REVERTED, "InstallState=2 after reboot")
    api.emit(ev(SetupStep.WHPX, "failed", problem=reverted), qapp)
    assert w.anyway_button.isHidden() and not w.retry_button.isHidden()
    w.reject()   # nothing is running any more: closes without asking
    assert not w.isVisible()


def test_quarantine_copies_the_antivirus_request(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                                 monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    w, api = made
    w.begin()
    gone = messages_ko.error(ErrorCode.SETUP_QUARANTINE, "qemu-system-x86_64.exe")
    assert gone.remedy is not None and gone.remedy.action is RemedyAction.COPY_IT_REQUEST
    api.emit(ev(SetupStep.EXTRACT, "failed", problem=gone), qapp)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w.request_button.click()
    copied = QApplication.clipboard().text()
    assert f"{tmp_path}\\sdk" in copied and "qemu-system-x86_64.exe" in copied


def test_restart_and_the_end(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup]) -> None:
    w, api = made
    w.begin()
    api.emit(ev(SetupStep.REBOOT, "waiting_user"), qapp)
    assert w.stack.currentWidget() is w.reboot_page
    api.emit(ev(SetupStep.DONE, "done", progress=1.0), qapp)
    assert w.stack.currentWidget() is w.done_page
    w.done_button.click()
    assert w.result() == QDialog.DialogCode.Accepted and api.cb is None


def test_closing_the_last_page_starts_the_phone_too(qapp: QApplication,
                                                     made: tuple[wizard.SetupWizard, FakeSetup]) -> None:
    w, api = made
    w.begin()
    api.emit(ev(SetupStep.DONE, "done", progress=1.0), qapp)
    w.reject()   # the close box on "setup is done" is the same as [Start]
    assert w.result() == QDialog.DialogCode.Accepted and ("cancel",) not in api.calls


def test_closing_asks_while_busy_and_stops_setup(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    w, api = made
    w.begin()
    answers = [QMessageBox.StandardButton.No]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers[0])
    w.reject()
    assert w.isVisible() and ("cancel",) not in api.calls        # kept going
    answers[0] = QMessageBox.StandardButton.Yes
    w.reject()
    assert not w.isVisible() and api.calls[-1] == ("cancel",) and api.cb is None


def test_signing_out_closes_without_asking(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    w, api = made
    w.begin()
    asked: list[object] = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a))
    w._logoff(None)   # type: ignore[arg-type]  # noqa: SLF001  (Windows' commitDataRequest)
    assert not w.isVisible() and asked == [] and api.calls[-1] == ("cancel",)


def test_declining_the_license_stops_setup(qapp: QApplication, made: tuple[wizard.SetupWizard, FakeSetup],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    w, api = made
    w.begin()
    api.pending = LICENSE
    api.emit(ev(SetupStep.LICENSE, "waiting_user"), qapp)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w.license_page.findChildren(type(w.agree))[0].click()        # [I do not agree]
    assert not w.isVisible() and ("accept", "sha-1", messages_ko.current_user()) not in api.calls
    assert api.calls[-1] == ("cancel",)


def test_resumed_by_runonce_starts_by_itself(qapp: QApplication, tmp_path: Path) -> None:
    api = FakeSetup()
    w = wizard.SetupWizard(api, tmp_path, lambda: None, start=True)
    assert api.calls == [("run",)] and w.stack.currentWidget() is w.work
    w.hide()


def test_the_wizard_in_english(qapp: QApplication, tmp_path: Path) -> None:
    i18n.set_language("en")
    try:
        w = wizard.SetupWizard(FakeSetup(), tmp_path, lambda: None)
        assert w.windowTitle() == "Orcus APK Emulator setup" and w.start_button.text() == "Start setup"
        assert w.agree.text() == "I agree"
    finally:
        i18n.set_language("ko")


def test_the_window_opens_setup_and_starts_the_phone_after_it(qapp: QApplication, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """[Open setup] / [Turn on the Windows Hypervisor Platform] on the engine panel (§10.5); not while a phone is
    on (a [Log] card can be older than the phone)."""
    s = FakeSessionApi()
    s.setup = FakeSetup()   # type: ignore[attr-defined]
    results = [QDialog.DialogCode.Rejected]
    opened: list[object] = []

    class Wizard:
        def __init__(self, api: object, home: Path, *a: object, **k: object) -> None:
            opened.append((api, home, k))

        def exec(self) -> QDialog.DialogCode:
            return results[0]
    monkeypatch.setattr(window, "SetupWizard", Wizard)
    w = window.MainWindow(s, Bridge(), tmp_path / "logs")   # type: ignore[arg-type]
    s.engine.st = replace(s.engine.st, state=EngineState.FAILED, text_key="engine.state.failed",
                          error=messages_ko.error(ErrorCode.PRE_NOT_INSTALLED))
    w.on_engine(s.engine.st)
    w.remedy(RemedyAction.REPAIR_SETUP)
    assert s.engine.retried == 0                          # closed before the end: nothing restarts
    results[0] = QDialog.DialogCode.Accepted
    w.remedy(RemedyAction.ENABLE_WHPX)
    assert s.engine.retried == 1 and opened[-1] == (s.setup, tmp_path, {"start": False})   # type: ignore[attr-defined]
    s.engine.st = replace(s.engine.st, state=EngineState.READY, text_key="engine.state.ready", error=None, epoch=1)
    w.on_engine(s.engine.st)
    w.remedy(RemedyAction.REPAIR_SETUP)
    assert len(opened) == 2 and s.engine.retried == 1 and "설치 마법사를 열지 않았습니다" in w.toast.text()
    w.quiet_close = True
    w.close()


def test_runonce_resumes_where_setup_was_started() -> None:
    p = OrcusPaths(Path(r"E:\Orcus"))
    gui = compose.resume_command(p, gui=True)
    cli = compose.resume_command(p)
    assert "--setup-resume" in gui and r"E:\Orcus" in gui and " -m orcus " in gui
    assert "orcus.cli setup --resume" in cli and r"E:\Orcus" in cli
