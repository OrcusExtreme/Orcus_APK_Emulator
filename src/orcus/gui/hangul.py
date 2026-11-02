"""Korean input bar under the phone (ARCHITECTURE.md §9.6): Windows IME composes, Enter sends.

Key events cannot carry Korean (S8), so the text goes through paste_text: setClipboard, 100 ms, the Paste key.
Enter sends and clears; Shift+Enter is a new line (MultiLine keeps it); Esc closes. The bar stays open between
sends. It overwrites the phone's clipboard, hence the tooltip.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QToolButton

from orcus.common.i18n import N_, t

TOOLTIP = N_("휴대폰 클립보드를 덮어씁니다. 비밀번호에는 쓰지 마세요.")
LINES = 2


class _Edit(QPlainTextEdit):
    submitted = Signal()
    escaped = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()   # the IME has already committed the syllable it was composing
            return
        if key == Qt.Key.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(event)


class HangulBar(QFrame):
    send = Signal(str)
    closed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.edit = _Edit()
        self.edit.setTabChangesFocus(True)
        self.edit.setPlaceholderText(t("여기에 쓰고 Enter · 줄바꿈 Shift+Enter · 닫기 Esc"))
        self.edit.setToolTip(t(TOOLTIP))
        lh = self.edit.fontMetrics().lineSpacing()
        self.edit.setFixedHeight(lh * LINES + 12)
        self.edit.submitted.connect(self._submit)
        self.edit.escaped.connect(self.close_bar)
        go = QPushButton(t("보내기"))
        go.setToolTip(t(TOOLTIP))
        go.clicked.connect(self._submit)
        shut = QToolButton()
        shut.setText(t("닫기"))
        shut.clicked.connect(self.close_bar)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.addWidget(QLabel(t("한글")))
        lay.addWidget(self.edit, 1)
        lay.addWidget(go)
        lay.addWidget(shut)
        self.hide()

    def open_bar(self) -> None:
        self.show()
        self.edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def close_bar(self) -> None:
        self.hide()
        self.closed.emit()

    def _submit(self) -> None:
        words = self.edit.toPlainText()
        if words:
            self.send.emit(words)
            self.edit.clear()
        self.edit.setFocus()
