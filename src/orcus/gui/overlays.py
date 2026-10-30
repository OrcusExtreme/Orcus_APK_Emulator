"""Feedback widgets over the phone (ARCHITECTURE.md §10.2): progress overlay, toast, banners, engine panel."""

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from orcus.common.messages_ko import text
from orcus.contracts import OrcusError, RemedyAction


class Overlay(QFrame):
    """Semi-transparent cover over the screen: a step sentence and, when known, a progress bar (§10.2)."""
    clicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet("Overlay{background:rgba(10,10,14,190);border-radius:6px} QLabel{color:white}")
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self.label.font()
        f.setPointSizeF(f.pointSizeF() * 1.2)
        self.label.setFont(f)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        lay.addStretch(1)
        self.hide()

    def show_text(self, words: str, progress: float | None = None) -> None:
        self.label.setText(words)
        self.bar.setVisible(progress is not None)
        if progress is not None:
            self.bar.setValue(int(progress * 100))
        self.raise_()
        self.show()

    def mousePressEvent(self, event: object) -> None:   # type: ignore[override]
        self.clicked.emit()


class Toast(QLabel):
    """Top-of-phone note for 3 s (§10.2 success/info)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:rgba(32,33,36,230);color:white;border-radius:12px;padding:6px 14px")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_text(self, words: str, ms: int = 3000) -> None:
        self.setText(words)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None:
            self.move((parent.width() - self.width()) // 2, 16)
        self.raise_()
        self.show()
        self._timer.start(ms)


class Banner(QFrame):
    """A warning that stays until closed or resolved (§10.2: software rendering, disk, reset failed ...)."""

    def __init__(self, words: str, *, red: bool = False, action: tuple[str, Callable[[], None]] | None = None,
                 on_close: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.setStyleSheet("Banner{background:%s;border-radius:6px} QLabel{color:%s}"
                           % (("#b3261e", "white") if red else ("#fff4ce", "#3b2f00")))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        label = QLabel(words)
        label.setWordWrap(True)
        lay.addWidget(label, 1)
        if action is not None:
            b = QPushButton(action[0])
            b.clicked.connect(action[1])
            lay.addWidget(b)
        close = QToolButton()
        close.setText("닫기")
        close.setAutoRaise(True)
        close.clicked.connect(self.close)
        if on_close is not None:
            close.clicked.connect(on_close)
        lay.addWidget(close)


class EnginePanel(QFrame):
    """Whole phone area when the engine failed (§10.2): cause, the one remedy, log tail (20 lines)."""
    remedy = Signal(object)    # RemedyAction
    open_log = Signal()
    copy_report = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet("EnginePanel{background:#f6f7f9;border:1px solid #d0d5dd;border-radius:8px}")
        self.title = QLabel("휴대폰을 켜지 못했습니다")
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.title.setFont(f)
        self.cause = QLabel()
        self.cause.setWordWrap(True)
        self.code = QLabel()
        self.code.setWordWrap(True)
        self.code.setStyleSheet("color:#52606d")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.primary = QPushButton()
        self.primary.clicked.connect(self._remedy)
        log_btn = QPushButton("로그 보기")
        log_btn.clicked.connect(self.open_log.emit)
        report = QPushButton("보고서 복사")
        report.clicked.connect(self.copy_report.emit)
        row = QHBoxLayout()
        row.addWidget(self.primary)
        row.addWidget(log_btn)
        row.addWidget(report)
        row.addStretch(1)
        self.tail = QPlainTextEdit()
        self.tail.setReadOnly(True)
        self.tail.setMaximumHeight(180)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(self.cause)
        lay.addWidget(self.code)
        lay.addLayout(row)
        lay.addWidget(self.tail)
        lay.addStretch(1)
        self._action: RemedyAction | None = None
        self.hide()

    def show_error(self, err: OrcusError) -> None:
        self.cause.setText(text(err.message_key))
        self.code.setText(f"{err.code}: {err.detail}" if err.detail else str(err.code))
        self._action = err.remedy.action if err.remedy else None
        self.primary.setVisible(err.remedy is not None)
        if err.remedy is not None:
            self.primary.setText(text(err.remedy.label_key))
        self.tail.setPlainText("\n".join(err.log_excerpt.splitlines()[-20:]))
        self.tail.setVisible(bool(err.log_excerpt))
        self.raise_()
        self.show()

    def _remedy(self) -> None:
        if self._action is not None:
            self.remedy.emit(self._action)
