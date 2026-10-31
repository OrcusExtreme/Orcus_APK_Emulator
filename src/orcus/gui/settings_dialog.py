"""Settings (ARCHITECTURE.md §10.4, minimal): toolbar [Settings], and the GPU banner's [Change GPU mode].

Only what changed is saved. A different GPU mode means a new golden (about 5 minutes): it is confirmed before
anything is saved, and the rebuild is asked of the engine only after that. Everything else applies at once,
because the window and the pipeline read the settings whenever they need them.
"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from orcus.contracts import GpuMode, SessionApi, Sensitivity

from .sensors import SENSITIVITY_NAMES

GPU_CHOICES = (
    (GpuMode.HOST, "host (기본)", "그래픽 카드로 그립니다. 가장 빠릅니다."),
    (GpuMode.HOST_HIDDEN, "host_hidden", "그래픽 카드로 그리되 숨은 창을 씁니다. host에서 화면이 검거나 소리가 안 날 때."),
    (GpuMode.SOFTWARE, "software", "그래픽 카드 없이 그립니다. 느리지만 그래픽 드라이버 문제를 피합니다."),
)
REBUILD_QUESTION = "GPU 모드를 바꾸면 휴대폰을 다시 준비합니다(약 5분). 저장된 앱과 데이터는 지워집니다. 계속할까요?"


class SettingsDialog(QDialog):
    def __init__(self, session: SessionApi, root: Path, open_logs: Callable[[], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.s = session
        st = session.settings()
        self.setWindowTitle("설정")
        lay = QVBoxLayout(self)

        typing = QGroupBox("입력")
        t = QVBoxLayout(typing)
        paste = QLabel("한글 입력: 붙여넣기 키 — 휴대폰 클립보드를 거쳐 보냅니다. 키 입력으로는 한글이 자모로 "
                       "깨져서 다른 방식은 없습니다.")
        paste.setWordWrap(True)
        self.esc_back = QCheckBox("Esc = 뒤로")
        self.esc_back.setChecked(st.input.esc_is_back)
        self.right_back = QCheckBox("오른쪽 클릭 = 뒤로")
        self.right_back.setChecked(st.input.right_click_back)
        for w in (paste, self.esc_back, self.right_back):
            t.addWidget(w)
        lay.addWidget(typing)

        sensors = QGroupBox("흔들기")
        row = QHBoxLayout(sensors)
        row.addWidget(QLabel("처음 감도"))
        self.sensitivity = QComboBox()
        for s in (Sensitivity.WEAK, Sensitivity.MODERATE, Sensitivity.STRONG):
            self.sensitivity.addItem(SENSITIVITY_NAMES[s], s)
        self.sensitivity.setCurrentIndex(max(0, self.sensitivity.findData(st.sensors.shake_default)))
        row.addWidget(self.sensitivity, 1)
        lay.addWidget(sensors)

        runs = QGroupBox("앱 실행")
        r = QVBoxLayout(runs)
        self.grant_all = QCheckBox("설치할 때 앱이 요청한 권한을 모두 허용")
        self.grant_all.setChecked(st.apk.grant_all)
        self.reset_each = QCheckBox("실행할 때마다 휴대폰 초기화 (수업·시험 모드)")
        self.reset_each.setChecked(st.apk.reset_before_each_run)
        self.ask_close = QCheckBox("닫을 때 앱 데이터가 지워진다고 알리기")
        self.ask_close.setChecked(st.ui.confirm_close_data)
        for w in (self.grant_all, self.reset_each, self.ask_close):
            r.addWidget(w)
        lay.addWidget(runs)

        gpu = QGroupBox("GPU 모드 (바꾸면 휴대폰을 다시 준비, 약 5분)")
        g = QVBoxLayout(gpu)
        self.gpu = QButtonGroup(self)
        for i, (mode, name, about) in enumerate(GPU_CHOICES):
            b = QRadioButton(f"{name} — {about}")
            b.setChecked(mode == st.engine.gpu_mode)
            self.gpu.addButton(b, i)
            g.addWidget(b)
        lay.addWidget(gpu)

        folder = QGroupBox("런타임 폴더")
        f = QHBoxLayout(folder)
        where = QLineEdit(str(root))
        where.setReadOnly(True)
        logs = QPushButton("로그 폴더 열기")
        logs.clicked.connect(lambda _=False: open_logs())
        f.addWidget(where, 1)
        f.addWidget(logs)
        lay.addWidget(folder)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.setMinimumWidth(520)

    def chosen_gpu(self) -> GpuMode:
        return GPU_CHOICES[max(0, self.gpu.checkedId())][0]

    def changes(self) -> dict[str, object]:
        st = self.s.settings()
        wanted: dict[str, object] = {
            "input.esc_is_back": self.esc_back.isChecked(),
            "input.right_click_back": self.right_back.isChecked(),
            "sensors.shake_default": Sensitivity(self.sensitivity.currentData()).value,
            "apk.grant_all": self.grant_all.isChecked(),
            "apk.reset_before_each_run": self.reset_each.isChecked(),
            "ui.confirm_close_data": self.ask_close.isChecked(),
            "engine.gpu_mode": self.chosen_gpu().value,
        }
        now: dict[str, object] = {
            "input.esc_is_back": st.input.esc_is_back, "input.right_click_back": st.input.right_click_back,
            "sensors.shake_default": st.sensors.shake_default.value, "apk.grant_all": st.apk.grant_all,
            "apk.reset_before_each_run": st.apk.reset_before_each_run,
            "ui.confirm_close_data": st.ui.confirm_close_data, "engine.gpu_mode": st.engine.gpu_mode.value,
        }
        return {k: v for k, v in wanted.items() if v != now[k]}

    def _apply(self) -> None:
        changes = self.changes()
        if not changes:
            self.accept()
            return
        result = self.s.preview_settings(changes)
        if result.needs_rebuild:
            if QMessageBox.question(self, "휴대폰 다시 준비", REBUILD_QUESTION) != QMessageBox.StandardButton.Yes:
                return
        elif result.needs_restart:
            QMessageBox.information(self, "설정", "이 설정은 휴대폰을 다시 켤 때 적용됩니다.")
        self.s.update_settings(changes)
        gpu = changes.get("engine.gpu_mode")
        if gpu is not None:
            self.s.engine.rebuild(f"GPU 모드를 {gpu}(으)로 바꿈")
        self.accept()


def focus_gpu(dialog: SettingsDialog) -> None:
    """Opened from the software-rendering banner: start on the GPU choice."""
    checked = dialog.gpu.checkedButton()
    if checked is not None:
        checked.setFocus(Qt.FocusReason.OtherFocusReason)
