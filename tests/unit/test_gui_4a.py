"""GUI without a device (offscreen Qt): light, proximity, battery, a text and a call from the [Sensors] tab, a new
phone shown as the phone has it, dropped files, the installed apps list, the diagnostic bundle, the camera picture."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from orcus.contracts import EngineState, InstalledApp, MediaResult, PhoneWorld
from orcus.gui import sensors, window
from test_gui import qapp   # noqa: F401  (a fixture)
from test_gui_3b import Api, ready


def test_world_and_phone_sections_drive_the_controls(qapp: QApplication, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    p, c = w.sensors, s.controls
    assert c.calls == []                                    # showing the phone's values sends nothing
    p.light_presets[1].click()                              # 실내
    p.light_spin.setValue(1234)
    p.near_box.setChecked(True)
    p.near_box.setChecked(False)
    p.battery_spin.setValue(15)
    p.charging_box.setChecked(False)                        # unplugged: the floor lifts it to 21 %
    p.battery_spin.setValue(5)
    p.charging_box.setChecked(True)
    p.battery_spin.setValue(5)
    assert c.calls == [("light", 400.0), ("light", 1234.0), ("proximity", 0.0),
                       ("proximity", sensors.PROXIMITY_FAR_CM), ("battery", 15, True), ("battery", 21, False),
                       ("battery", 21, True), ("battery", 5, True)]
    c.calls.clear()
    assert p.call_button.isEnabled() and not p.sms_button.isEnabled()   # no text yet
    p.sms_edit.setText("  안녕 Orcus  ")
    p.sms_button.click()
    p.call_button.click()
    p.hangup_button.click()
    assert c.calls == [("sms", sensors.DEFAULT_NUMBER, "안녕 Orcus"), ("call_in", sensors.DEFAULT_NUMBER),
                       ("call_end", sensors.DEFAULT_NUMBER)]
    p.number_edit.setText("12")                             # too short for a number
    assert not (p.call_button.isEnabled() or p.hangup_button.isEnabled() or p.sms_button.isEnabled())
    p.number_edit.setText("+821012345678")
    assert p.call_button.isEnabled() and p.sms_button.isEnabled()
    w.on_engine(replace(s.engine.st, state=EngineState.STOPPED))
    assert not (p.light_spin.isEnabled() or p.call_button.isEnabled() or p.sms_edit.isEnabled())
    w.close()


def test_a_new_phone_shows_what_the_phone_has(qapp: QApplication, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    s.controls.world = PhoneWorld(123.0, 0.0, 15, True)     # a kept phone, saved that way (§6.3)
    w = ready(s, tmp_path, monkeypatch)
    p = w.sensors
    assert (p.light_spin.value(), p.near_box.isChecked(), p.battery_spin.value(), p.charging_box.isChecked()) \
        == (123, True, 15, True)
    assert s.controls.calls == []
    w.close()


def test_dropped_pictures_go_to_the_session_and_say_where_they_went(qapp: QApplication, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """Any number of pictures, sounds and videos, or one app; anything else is not taken (§7.1)."""
    s = Api()
    w = ready(s, tmp_path, monkeypatch)

    kept: list[QMimeData] = []   # the event does not own its data: freed with the helper's frame, it hung

    def drop(*names: str) -> QDropEvent:
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path / n)) for n in names])
        kept.append(mime)
        return QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                          Qt.KeyboardModifier.NoModifier)
    for names in (("a.jpg", "b.MP3", "c.mp4"), ("x.apk",), ("x.apk", "y.apk"), ("a.jpg", "x.apk")):
        w.dropEvent(drop(*names))
    assert s.opened == [[tmp_path / "a.jpg", tmp_path / "b.MP3", tmp_path / "c.mp4"], [tmp_path / "x.apk"]]
    w._media_done(MediaResult("고양이.jpg", "/sdcard/Pictures/고양이.jpg", None))   # noqa: SLF001
    assert "사진에 넣었습니다: /sdcard/Pictures/고양이.jpg" in w.toast.text() and "ImagePicker" in w.toast.text()
    w.close()


def test_the_installed_apps_list_runs_and_removes(qapp: QApplication, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The [App] tab: the list from the service keeps the chosen app; [Run], [Remove] (asked first), refresh."""
    s = Api()
    w = ready(s, tmp_path, monkeypatch)
    p = w.app_panel
    assert p.apps_list.isHidden() and not p.apps_empty.isHidden()
    hello, other = InstalledApp("appinventor.ai_x.HelloPurr", "HelloPurr"), InstalledApp("com.example.a", "com.example.a")
    w.bridge.on_apps((hello, other))
    assert [p.apps_list.item(i).text() for i in range(p.apps_list.count())] == ["HelloPurr", "com.example.a"]
    p.apps_list.setCurrentRow(0)
    w.bridge.on_apps((hello,))                               # a refresh keeps the choice
    assert p.selected_app() == "appinventor.ai_x.HelloPurr"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    for key in ("run", "remove", "refresh"):
        p._installed_action(key)   # noqa: SLF001  (the three buttons)
    assert s.apps.calls[-3:] == ["run_installed", "remove_app", "refresh_apps"]
    w.close()


def test_the_diagnostic_bundle_is_made_off_the_gui_thread_and_shown(qapp: QApplication, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    s = Api()
    bundle = tmp_path / "orcus-diag-20261031-120000.zip"
    threads: list[str] = []

    def make() -> Path:
        threads.append(threading.current_thread().name)
        return bundle
    s.make_diagnostics = make   # type: ignore[attr-defined]
    opened: list[list[str]] = []
    monkeypatch.setattr(window.subprocess, "Popen", lambda argv, *a, **k: opened.append(argv))
    w = ready(s, tmp_path, monkeypatch)
    w.app_panel.action.emit("diag")
    end = time.monotonic() + 5
    while not opened and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert threads == ["diag"] and opened and opened[0][-1] == f"/select,{bundle}"
    assert "진단 묶음을 만들었습니다" in w.toast.text()
    w.close()


def test_the_camera_picture_is_chosen_scaled_and_reset(qapp: QApplication, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """[Pick a picture]: a PNG at most 1280 px a side goes to the session; [Default picture]: the bars."""
    s = Api()
    put: list[bytes | None] = []
    s.set_camera_image = put.append   # type: ignore[attr-defined]
    w = ready(s, tmp_path, monkeypatch)
    big = tmp_path / "고양이.png"
    img = QImage(2000, 1000, QImage.Format.Format_RGB32)
    img.fill(0x2040E0)
    assert img.save(str(big))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(big), ""))
    w.sensors.camera_buttons[0].click()
    shown = QImage.fromData(put[-1] or b"")
    assert (shown.width(), shown.height()) == (1280, 640) and "고양이.png" in w.sensors.camera_label.text()
    w.sensors.camera_buttons[1].click()
    assert put[-1] is None and "기본 그림" in w.sensors.camera_label.text()
    w.close()
