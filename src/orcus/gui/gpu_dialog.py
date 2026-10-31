"""The small GPU mode dialog of 3a (ARCHITECTURE.md §5.6, §10.4): toolbar [Settings] and the software banner.

A different mode means a new golden (about 3 minutes), so the change is confirmed before it is saved; the
rebuild itself is asked of the engine only after that.
"""

from PySide6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QLabel, QMessageBox, QRadioButton, QVBoxLayout

from orcus.contracts import GpuMode, SessionApi

CHOICES = (
    (GpuMode.HOST, "host (기본)", "그래픽 카드로 그립니다. 가장 빠릅니다."),
    (GpuMode.HOST_HIDDEN, "host_hidden", "그래픽 카드로 그리되 숨은 창을 씁니다. host에서 화면이 검거나 소리가 안 날 때."),
    (GpuMode.SOFTWARE, "software", "그래픽 카드 없이 그립니다. 느리지만 그래픽 드라이버 문제를 피합니다."),
)


class GpuDialog(QDialog):
    def __init__(self, session: SessionApi, parent: object = None) -> None:
        super().__init__(parent)   # type: ignore[arg-type]
        self.s = session
        self.setWindowTitle("GPU 모드")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("휴대폰 화면을 그리는 방법을 고르세요. 바꾸면 휴대폰을 다시 준비합니다(약 5분)."))
        self.group = QButtonGroup(self)
        current = session.settings().engine.gpu_mode
        for i, (mode, name, about) in enumerate(CHOICES):
            r = QRadioButton(f"{name} — {about}")
            r.setChecked(mode == current)
            self.group.addButton(r, i)
            lay.addWidget(r)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def chosen(self) -> GpuMode:
        return CHOICES[max(0, self.group.checkedId())][0]

    def _apply(self) -> None:
        mode = self.chosen()
        if mode == self.s.settings().engine.gpu_mode:
            self.accept()
            return
        change = {"engine.gpu_mode": mode.value}
        if self.s.preview_settings(change).needs_rebuild:
            answer = QMessageBox.question(self, "휴대폰 다시 준비",
                                          "GPU 모드를 바꾸면 휴대폰을 다시 준비합니다(약 5분). 저장된 앱과 데이터는 "
                                          "지워집니다. 계속할까요?")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.s.update_settings(change)
        self.s.engine.rebuild(f"GPU 모드를 {mode.value}(으)로 바꿈")
        self.accept()
