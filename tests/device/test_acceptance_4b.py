"""Acceptance on this PC (plan.md §3): the setup wizard against the real setup steps, and the
installer from install to the first app.

Run: py tools/dev.py test -m device tests/device/test_acceptance_4b.py -s   (after `py tools/dev.py build --installer`)
Lines starting with RESULT are for plan.md §4.
- an empty folder: the wizard goes through the PC check to Google's license, shows its whole text and keeps
  [I agree] off until it was scrolled through; the test then closes it. It never agrees (★D2): the person does,
  on the new Windows account of the acceptance test.
- this PC's installed folder: the wizard runs every step again and ends without asking anything (A1).
- the installer: installs into D:\\OrcusAPK\\app beside the existing phone, the packaged smoke passes from there,
  the "Open with" command starts the window with HelloPurr and it runs; the uninstaller then removes the program and
  keeps the phone (its default).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
import shutil
import subprocess
import time
import winreg
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from orcus import __version__
from orcus.app import compose
from orcus.common import winjob
from orcus.common.paths import OrcusPaths
from orcus.contracts import SetupStep
from orcus.gui import wizard
from orcus.setup import license
from test_acceptance_3a import result, until
from test_apk_real import HELLO

DIST = Path(r"D:\OrcusDev\dist")
SETUP = DIST / f"OrcusAPK-Setup-{__version__}.exe"
EMPTY_HOME = Path(r"D:\OrcusWizardTest")
LOGS = Path(r"D:\OrcusDev\tmp")
APPID = r"{6C1D3F8A-4E2B-4B7C-9A51-2D8E7F0A3B64}_is1"
PROGID_COMMAND = r"Software\Classes\OrcusAPK.apk\shell\open\command"
ACCOUNT = re.compile(r"appinventor\.ai_[^.\s]+")

pytestmark = [pytest.mark.device]


def masked(text: str) -> str:
    """The fixtures were built with the user's App Inventor account: never print it."""
    return ACCOUNT.sub("appinventor.ai_<계정>", text)


@pytest.fixture(scope="module")
def qapp() -> Iterator[QApplication]:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    yield app


def pump(app: QApplication, done: object, timeout_s: float, what: str) -> None:
    end = time.monotonic() + timeout_s
    while not done():   # type: ignore[operator]
        assert time.monotonic() < end, f"timed out: {what}"
        app.processEvents()
        time.sleep(0.05)


@pytest.mark.timeout(300)
def test_an_empty_folder_reaches_the_license_and_waits_for_a_person(qapp: QApplication,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    assert not EMPTY_HOME.exists(), f"{EMPTY_HOME} is left from an earlier run"
    compose.ensure_job()
    p = OrcusPaths(EMPTY_HOME)
    api = compose.build_setup(p, compose.load_settings(p), gui=True)
    w = wizard.SetupWizard(api, EMPTY_HOME, lambda: None)
    w.show()
    try:
        t = time.monotonic()
        w.begin()
        pump(qapp, lambda: w.stack.currentWidget() in (w.license_page, w.failed_page), 180, "the license page")
        assert w.stack.currentWidget() is w.license_page, w.failed_words.text() + " " + w.failed_detail.text()
        seconds = time.monotonic() - t
        text = w.license_text.toPlainText()
        assert "Android Software Development Kit License Agreement" in text and "14.7" in text
        assert "참고용 요약" in w.license_summary.text() and "emulator" in w.license_packages.text()
        assert not w.agree.isEnabled()
        bar = w.license_text.verticalScrollBar()
        bar.setValue(bar.maximum())
        pump(qapp, w.agree.isEnabled, 5, "[I agree] after scrolling")
        result("wizard_empty_folder", to_license_s=round(seconds, 1), license_chars=len(text),
               agree_after_scroll=w.agree.isEnabled())
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        w.reject()   # closed at the license: nothing agreed, setup cancelled
        assert api.join(10)
        assert license.load_records(p) == []
    finally:
        w.hide()
        shutil.rmtree(EMPTY_HOME, ignore_errors=True)


@pytest.mark.timeout(300)
def test_the_installed_folder_needs_nothing(qapp: QApplication) -> None:
    compose.ensure_job()
    p = compose.make_paths()
    api = compose.build_setup(p, compose.load_settings(p), gui=True)
    w = wizard.SetupWizard(api, p.root, lambda: None)
    shown: list[str] = []
    pages = {w.welcome: "welcome", w.work: "work", w.license_page: "license", w.whpx_page: "whpx",
             w.failed_page: "failed", w.reboot_page: "reboot", w.done_page: "done"}
    names = {w.stack.indexOf(page): name for page, name in pages.items()}
    w.stack.currentChanged.connect(lambda i: shown.append(names.get(i, "?")))
    w.show()
    try:
        t = time.monotonic()
        w.begin()
        pump(qapp, lambda: w.stack.currentWidget() in (w.done_page, w.failed_page, w.license_page, w.whpx_page,
                                                       w.reboot_page), 120, "the end of setup")
        assert w.stack.currentWidget() is w.done_page, w.failed_words.text() + " " + w.failed_detail.text()
        states = {ev.step: ev.state for ev in api.events()}
        assert all(states[s] == "done" for s in SetupStep if s is not SetupStep.PROVISION)
        assert set(shown) <= {"work", "done"}
        result("wizard_installed_folder", seconds=round(time.monotonic() - t, 1), pages=" > ".join(shown))
    finally:
        w.hide()


def _value(path: str, name: str = "") -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return str(winreg.QueryValueEx(k, name)[0])
    except OSError:
        return None


def _sdk_processes(root: Path) -> list[str]:
    return sorted({Path(image).name for _pid, image in winjob.processes_under(root / "sdk")})


def _desktop_env() -> dict[str, str]:
    """The environment a double-click gives: without this file's QT_QPA_PLATFORM=offscreen, which the packaged
    window cannot use (the trimmed build has only the windows platform plugin)."""
    return {k: v for k, v in os.environ.items() if k.upper() != "QT_QPA_PLATFORM"}


@pytest.mark.timeout(1800)
@pytest.mark.skipif(not SETUP.exists() or not HELLO.exists(), reason="py tools/dev.py build --installer first")
def test_install_smoke_open_with_and_uninstall_keeping_the_phone() -> None:
    root = compose.make_paths().root
    app = root / "app"
    assert not app.exists(), f"{app} already exists: not ours to overwrite"
    assert not _sdk_processes(root), "another phone is running"
    t = time.monotonic()
    code = subprocess.run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/HOME={root}",
                           "/TASKS=apkopenwith", f"/LOG={LOGS / 'inst-4b.log'}"], timeout=600).returncode
    installed_s = time.monotonic() - t
    gui: subprocess.Popen[bytes] | None = None
    try:
        assert code == 0 and (app / "OrcusAPK.exe").exists()
        assert (app / "orcus_home.txt").read_text(encoding="utf-8-sig") == str(root)
        smoke = subprocess.run([str(app / "orcus-cli.exe"), "smoke", "--apk", str(HELLO)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=1200, env=_desktop_env())
        print(masked(smoke.stdout[-3000:]))
        assert smoke.returncode == 0, masked(smoke.stdout[-2000:])

        command = _value(PROGID_COMMAND)
        assert command == f'"{app / "OrcusAPK.exe"}" "%1"'
        gui_log = root / "logs" / f"gui-{time.strftime('%Y%m%d')}.log"   # common.log: one file per name and day
        offset = gui_log.stat().st_size if gui_log.exists() else 0
        started = time.time()
        # what Explorer runs for "Open with"
        gui = subprocess.Popen([str(app / "OrcusAPK.exe"), str(HELLO)], env=_desktop_env())
        window = gui
        running = ""

        def app_runs() -> bool:
            nonlocal running
            assert window.poll() is None, "the window ended"
            if gui_log.exists():
                with gui_log.open("rb") as f:
                    f.seek(offset)
                    for line in f.read().decode("utf-8", errors="replace").splitlines():
                        if "HelloPurr running" in line:
                            running = masked(line)
                            return True
            return False
        until(app_runs, 600, "HelloPurr running in the installed window")
        opened_s = time.time() - started
        shot = subprocess.run([str(app / "orcus-cli.exe"), "engine", "shot", "--out", str(LOGS / "shot-4b.png")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        assert shot.returncode == 0 and (LOGS / "shot-4b.png").stat().st_size > 10_000
        window.kill()   # as a crash would: the window's Job takes the phone along (§1.2), nothing is saved
        window.wait(15)
        until(lambda: not _sdk_processes(root), 30, "no SDK process after the window")
        result("installer", install_s=round(installed_s, 1), smoke=smoke.stdout.strip().splitlines()[-1][:120],
               open_with_to_running_s=round(opened_s, 1), running=running[-120:])
    finally:
        if gui is not None and gui.poll() is None:   # files in use stay behind an uninstall
            gui.kill()
            gui.wait(15)
        unins = app / "unins000.exe"
        if unins.exists():
            subprocess.run([str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                            f"/LOG={LOGS / 'unins-4b.log'}"], timeout=600)
            until(lambda: not app.exists(), 60, "the program folder removed")
    assert all((root / d).is_dir() for d in ("sdk", "avd", "state")), "the phone and the SDK stay"
    assert _value(PROGID_COMMAND) is None and _value(rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APPID}",
                                                     "UninstallString") is None
    result("uninstall", app_removed=not app.exists(), phone_kept=(root / "avd").is_dir())
