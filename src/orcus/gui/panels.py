"""Right-hand panels (ARCHITECTURE.md §10.3): [App] card with chips and actions, [Log] problem cards and raw log."""

import html
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from orcus.common.messages_ko import text


def plain_box(parent: QWidget, title: str, words: str) -> None:
    """An information box that never renders its text as HTML (it can quote an app's or the emulator's text)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(words)
    box.exec()
from orcus.contracts import ApkInfo, InstalledApp, Issue, IssueLevel, LogEntry, OrcusError

from . import dialogs

LEVEL_STYLE = {IssueLevel.BLOCK: "background:#b3261e;color:white", IssueLevel.WARN: "background:#f9dea0;color:#3b2f00",
               IssueLevel.INFO: "background:#dde3ea;color:#1f2933"}
LOG_COLORS = {"E": "#c62828", "F": "#c62828", "W": "#b26a00", "I": "#1f2933", "D": "#52606d", "V": "#7b8794"}
RAW_LINES = 2000


class AppPanel(QWidget):
    action = Signal(str)   # grant_all | clear_data | uninstall | match_orientation | copy_report | diag
    installed = Signal(str, str)   # [P4] (run | remove | refresh, package)

    def __init__(self) -> None:
        super().__init__()
        self.icon = QLabel()
        self.icon.setFixedSize(64, 64)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.3)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.facts = QLabel()
        self.facts.setWordWrap(True)
        self.facts.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # the manifest's label and package are untrusted: `<img src=//host/x>` loaded from SMB as rich text (3a)
        for label in (self.title, self.facts):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.era = QLabel()
        self.era.setStyleSheet("background:#e8f0fe;color:#174ea6;border-radius:8px;padding:2px 8px")
        self.empty = QLabel("여기에 .apk를 끌어다 놓거나 [APK 열기]를 누르세요.")
        self.empty.setWordWrap(True)
        head = QHBoxLayout()
        head.addWidget(self.icon)
        names = QVBoxLayout()
        names.addWidget(self.title)
        names.addWidget(self.era, 0, Qt.AlignmentFlag.AlignLeft)
        head.addLayout(names, 1)
        self.chips = QVBoxLayout()
        self.chips.setSpacing(4)
        buttons = QVBoxLayout()
        for key, label in (("grant_all", "이 앱 권한 모두 허용"), ("clear_data", "데이터 지우고 실행"),
                           ("uninstall", "제거"), ("match_orientation", "화면 방향 맞추기"), ("copy_report", "보고서 복사"),
                           ("diag", "진단 묶음 만들기")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            buttons.addWidget(b)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(head)
        body.addWidget(self.facts)
        body.addLayout(self.chips)
        body.addSpacing(8)
        body.addLayout(buttons)
        lay = QVBoxLayout(self)
        lay.addWidget(self.empty)
        lay.addWidget(self.body)
        lay.addWidget(self._installed_box())
        lay.addStretch(1)
        self.body.hide()

    def _installed_box(self) -> QGroupBox:
        """[P4] The apps in the phone: run one again without its file, or remove it."""
        box = QGroupBox("휴대폰에 설치된 앱")
        self.apps_list = QListWidget()
        self.apps_list.setMaximumHeight(150)
        self.apps_list.itemDoubleClicked.connect(lambda _item: self._installed_action("run"))
        self.apps_empty = QLabel("아직 없습니다. 앱을 넣으면 여기에 남습니다.")
        self.apps_empty.setStyleSheet("color:#52606d")
        row = QHBoxLayout()
        for key, label in (("run", "실행"), ("remove", "지우기"), ("refresh", "새로 고침")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self._installed_action(k))
            row.addWidget(b)
        lay = QVBoxLayout(box)
        lay.addWidget(self.apps_empty)
        lay.addWidget(self.apps_list)
        lay.addLayout(row)
        self.show_installed(())
        return box

    def show_installed(self, apps: tuple[InstalledApp, ...]) -> None:
        chosen = self.selected_app()
        self.apps_list.clear()
        for app in apps:
            item = QListWidgetItem(app.name)
            item.setData(Qt.ItemDataRole.UserRole, app.package)
            item.setToolTip(app.package)
            self.apps_list.addItem(item)
            if app.package == chosen:
                self.apps_list.setCurrentItem(item)
        self.apps_list.setVisible(bool(apps))
        self.apps_empty.setVisible(not apps)

    def selected_app(self) -> str | None:
        item = self.apps_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _installed_action(self, key: str) -> None:
        package = self.selected_app()
        if key == "refresh" or package is not None:
            self.installed.emit(key, package or "")

    def show_app(self, info: ApkInfo | None, issues: tuple[Issue, ...], fallback_name: str = "") -> None:
        self.empty.hide()
        self.body.show()
        pm = QPixmap()
        if info is not None and info.icon_png and pm.loadFromData(info.icon_png, "PNG"):   # no other image plugin
            self.icon.setPixmap(pm.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        else:
            self.icon.clear()
        if info is None:
            self.title.setText(fallback_name or "앱")
            self.facts.setText("앱 정보를 읽지 못했습니다. 설치한 뒤 이름을 찾습니다.")
            self.era.hide()
        else:
            self.title.setText(info.label)
            self.facts.setText(f"{info.package}\n버전 {info.version_name or '-'} ({info.version_code}) · "
                               f"minSdk {info.min_sdk} · targetSdk {info.target_sdk}")
            self.era.setText(f"App Inventor {info.ai_era}" if info.is_app_inventor and info.ai_era
                             else info.ai_era or "App Inventor 앱이 아님")
            self.era.show()
        while self.chips.count():
            item = self.chips.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for issue in issues:
            words = text(issue.text_key)
            chip = QToolButton()
            chip.setText(words if len(words) <= 34 else words[:32] + "…")
            chip.setToolTip(words)
            chip.setStyleSheet(f"{LEVEL_STYLE[issue.level]};border-radius:10px;padding:3px 8px;text-align:left")
            chip.clicked.connect(lambda _=False, w=words: plain_box(self, "PC 휴대폰 안내", w))
            self.chips.addWidget(chip)


class ProblemCard(QFrame):
    def __init__(self, err: OrcusError, when: float, on_remedy: Callable[[OrcusError], None]) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QVBoxLayout(self)
        top = QLabel(f"{time.strftime('%H:%M:%S', time.localtime(when))} · {err.code}")
        top.setStyleSheet("color:#52606d")
        cause = QLabel(text(err.message_key))
        cause.setWordWrap(True)
        lay.addWidget(top)
        lay.addWidget(cause)
        if err.detail:
            d = QLabel()
            d.setTextFormat(Qt.TextFormat.PlainText)   # crash and runtime-error text comes from the app (3a review)
            d.setText(err.detail if len(err.detail) <= 160 else err.detail[:157] + "…")
            d.setWordWrap(True)
            d.setStyleSheet("color:#52606d")
            d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(d)
        row = QHBoxLayout()
        if err.remedy is not None:
            b = QPushButton(text(err.remedy.label_key))
            b.clicked.connect(lambda: on_remedy(err))
            row.addWidget(b)
        more = QPushButton("자세히")
        more.clicked.connect(lambda: self._details(err))
        row.addWidget(more)
        row.addStretch(1)
        lay.addLayout(row)

    def _details(self, err: OrcusError) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("자세히")
        box.setText(text(err.message_key))
        # the detail is the app's or the emulator's text: the informative label rendered it as HTML with links that
        # open (file://host/x.hta), so it goes in the plain detailed-text area instead (3a review)
        detail = f"{err.code}: {err.detail}" if err.detail else str(err.code)
        box.setDetailedText(detail + (f"\n\n{err.log_excerpt}" if err.log_excerpt else ""))
        box.exec()


class LogPanel(QWidget):
    remedy = Signal(object)   # OrcusError whose remedy button was pressed

    def __init__(self) -> None:
        super().__init__()
        self.toggle = QToolButton()
        self.toggle.setText("원본 로그")
        self.toggle.setCheckable(True)
        self.save = QPushButton("저장")
        self.clear_btn = QPushButton("비우기")
        top = QHBoxLayout()
        top.addWidget(QLabel("문제"))
        top.addStretch(1)
        top.addWidget(self.toggle)
        top.addWidget(self.save)
        top.addWidget(self.clear_btn)
        self.cards = QListWidget()
        self.cards.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setMaximumBlockCount(RAW_LINES)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.raw.setFont(mono)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.cards)
        self.stack.addWidget(self.raw)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.stack)
        self.toggle.toggled.connect(lambda on: self.stack.setCurrentIndex(1 if on else 0))
        self.save.clicked.connect(self._save)
        self.clear_btn.clicked.connect(self._clear)
        self.count = 0

    def add_problem(self, err: OrcusError) -> None:
        card = ProblemCard(err, time.time(), self.remedy.emit)
        item = QListWidgetItem()
        item.setSizeHint(card.sizeHint())
        self.cards.insertItem(0, item)
        self.cards.setItemWidget(item, card)
        self.count += 1

    def append_log(self, e: LogEntry) -> None:
        stamp = time.strftime("%H:%M:%S", time.localtime(e.ts_us / 1_000_000))
        color = LOG_COLORS.get(e.level, "#1f2933")
        self.raw.appendHtml(f'<span style="color:{color}">{stamp} {e.pid:5d} {e.level} '
                            f'{html.escape(e.tag)}: {html.escape(e.msg)}</span>')

    def _clear(self) -> None:
        if self.stack.currentIndex() == 1:
            self.raw.clear()
        else:
            self.cards.clear()
            self.count = 0

    def _save(self) -> None:
        name = dialogs.save_file(self, "로그 저장", str(Path.home() / "orcus-log.txt"), "텍스트 (*.txt)")
        if name:
            try:
                Path(name).write_text(self.raw.toPlainText(), encoding="utf-8")
            except OSError as e:   # no console in the windowed build: say so instead of failing silently
                plain_box(self, "로그 저장", f"저장하지 못했습니다: {e}")
