"""The phone around the screen (ARCHITECTURE.md §10.1): a plain rounded bezel drawn with QPainter (no maker
marks), the ◁ ○ ▢ row below it (D7: the guest also shows its own 3-button bar) and the power and volume keys.

Key glyphs are drawn shapes, not emoji (§10.6). A key held longer than 0.6 s is a long press (power menu).
"""

import time
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from orcus.contracts import HwKey

from .screen import ScreenView

BEZEL = 12
RADIUS = 26
NAV_H = 40
SIDE_W = 44
LONG_PRESS_S = 0.6
FRAME_COLOR = QColor(28, 28, 32)
EDGE_COLOR = QColor(70, 70, 78)


def glyph(kind: str, size: int = 22) -> QIcon:
    """Drawn in the palette's text color: Windows dark mode gives Qt a dark palette."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QGuiApplication.palette().color(QPalette.ColorRole.ButtonText), 2))
    s = float(size)
    match kind:
        case "back":
            p.drawPolygon(QPolygonF([QPointF(s * 0.7, s * 0.2), QPointF(s * 0.7, s * 0.8), QPointF(s * 0.25, s * 0.5)]))
        case "home":
            p.drawEllipse(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.56))
        case "recents":
            p.drawRoundedRect(QRectF(s * 0.25, s * 0.25, s * 0.5, s * 0.5), 2, 2)
        case "power":
            p.drawArc(QRectF(s * 0.2, s * 0.22, s * 0.6, s * 0.6), 120 * 16, 300 * 16)
            p.drawLine(QPointF(s * 0.5, s * 0.12), QPointF(s * 0.5, s * 0.5))
        case "vol_up" | "vol_down":
            path = QPainterPath()
            path.moveTo(s * 0.15, s * 0.4)
            path.lineTo(s * 0.3, s * 0.4)
            path.lineTo(s * 0.5, s * 0.22)
            path.lineTo(s * 0.5, s * 0.78)
            path.lineTo(s * 0.3, s * 0.6)
            path.lineTo(s * 0.15, s * 0.6)
            path.closeSubpath()
            p.drawPath(path)
            p.drawLine(QPointF(s * 0.62, s * 0.5), QPointF(s * 0.9, s * 0.5))
            if kind == "vol_up":
                p.drawLine(QPointF(s * 0.76, s * 0.36), QPointF(s * 0.76, s * 0.64))
        case _:
            pass
    p.end()
    return QIcon(pm)


class KeyButton(QToolButton):
    """Sends its key on release; held over LONG_PRESS_S it is a long press."""
    pressed_key = Signal(object, bool)   # HwKey, long_press

    def __init__(self, key: HwKey, icon: str, tip: str) -> None:
        super().__init__()
        self._key = key
        self._down_at = 0.0
        self.setIcon(glyph(icon))
        self.setIconSize(QSize(22, 22))
        self.setToolTip(tip)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # the screen keeps the keyboard
        super().pressed.connect(self._down)
        super().released.connect(self._up)

    def _down(self) -> None:
        self._down_at = time.monotonic()

    def _up(self) -> None:
        self.pressed_key.emit(self._key, time.monotonic() - self._down_at >= LONG_PRESS_S)


class Bezel(QWidget):
    """Paints the frame; the screen sits inside with BEZEL margins."""

    def __init__(self, screen: ScreenView) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(BEZEL, BEZEL, BEZEL, BEZEL)
        lay.addWidget(screen)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(EDGE_COLOR, 2))
        p.setBrush(FRAME_COLOR)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS, RADIUS)


class PhoneView(QWidget):
    def __init__(self, screen: ScreenView, on_key: Callable[[HwKey, bool], None]) -> None:
        super().__init__()
        self.view = screen   # not `screen`: QWidget.screen() is the monitor
        self.bezel = Bezel(screen)
        nav = QHBoxLayout()
        nav.setContentsMargins(0, 4, 0, 0)
        nav.addStretch(1)
        side = QVBoxLayout()
        side.setContentsMargins(6, 40, 0, 0)
        self.keys: list[KeyButton] = []
        for key, icon, tip, box in ((HwKey.BACK, "back", "뒤로 (Esc · 오른쪽 클릭)", nav),
                                    (HwKey.HOME, "home", "홈 (Ctrl+Shift+H · 가운데 클릭)", nav),
                                    (HwKey.RECENTS, "recents", "최근 앱 (Ctrl+Shift+R)", nav),
                                    (HwKey.POWER, "power", "전원 (길게 누르면 전원 메뉴)", side),
                                    (HwKey.VOL_UP, "vol_up", "볼륨 크게", side),
                                    (HwKey.VOL_DOWN, "vol_down", "볼륨 작게", side)):
            b = KeyButton(key, icon, tip)
            b.pressed_key.connect(on_key)
            box.addWidget(b)
            if box is nav:
                nav.addSpacing(28)
            self.keys.append(b)
        nav.addStretch(1)
        side.addStretch(1)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.bezel, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addLayout(nav)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addLayout(column)
        row.addLayout(side)

    def set_screen_size(self, size: QSize) -> None:
        self.view.setFixedSize(size)
        self.bezel.setFixedSize(size.width() + 2 * BEZEL, size.height() + 2 * BEZEL)

    @staticmethod
    def chrome() -> QSize:
        """Space the phone needs around the screen itself."""
        return QSize(2 * BEZEL + SIDE_W, 2 * BEZEL + NAV_H)
