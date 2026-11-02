"""[Sensors] tab (ARCHITECTURE.md §10.3, §9.7, §9.8): shake with its sensitivity, turning, a tilt pad, location,
the microphone, and [P4] light, proximity, battery, an incoming text and call. Next to every control is the App
Inventor event it causes.

The panel only emits; the window turns that into DeviceControls calls (all of them enqueue and return).
"""

from PySide6.QtCore import QPointF, QRectF, QRegularExpression, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from orcus.contracts import BATTERY_MIN_UNPLUGGED, PhoneWorld, Rotation, Sensitivity

SHAKE_REPEAT_MS = 450   # a held shake button: longer than App Inventor's 400 ms MinimumInterval (§9.7)
TILT_MAX_DEG = 20.0     # the controls clamp to 20° too: 26° flips a landscape frame to portrait (S7)
TILT_EMIT_MS = 50       # at most 20 updates a second
SENSITIVITY_NAMES = {Sensitivity.WEAK: "약하게 (Weak)", Sensitivity.MODERATE: "보통 (Moderate)",
                     Sensitivity.STRONG: "강하게 (Strong)"}
POSTURE_NAMES = {Rotation.PORTRAIT: "세로", Rotation.LANDSCAPE: "가로 (왼쪽으로 돌림)",
                 Rotation.REVERSE_PORTRAIT: "거꾸로", Rotation.REVERSE_LANDSCAPE: "가로 (오른쪽으로 돌림)"}
LOCATION_HINTS = (
    "App Inventor LocationSensor 기본값은 TimeInterval 60000 ms·DistanceInterval 5 m라서, 첫 위치 뒤에는 약 1분마다·"
    "5 m 이상 움직였을 때만 LocationChanged가 옵니다. 빨리 보려면 TimeInterval을 1000으로 바꾸세요.",
    "권한 대화상자에서 '대략적인 위치'를 고르면 10분에 한 번만 갱신됩니다 — [이 앱 권한 모두 허용]으로 정확한 위치를 허용하세요.",
)
MIC_HINT = ("켜면 이 앱은 다음 실행에서도 켜집니다. 녹음이 조용하면 Windows 설정 > 개인 정보 > 마이크에서 "
            "'데스크톱 앱이 마이크에 액세스하도록 허용'을 켜세요.")
LIGHT_PRESETS = (("어두움", 10), ("실내", 400), ("햇빛", 30000))   # lux
PROXIMITY_FAR_CM = 1.0   # the Goldfish proximity sensor's far value (the golden's); 0 = something near
SMS_MAX = 70             # one text in Korean (UCS-2)
PHONE_NUMBER = QRegularExpression(r"\+?[0-9]{3,15}")
DEFAULT_NUMBER = "01012345678"
SMS_HINT = ("→ Texting.MessageReceived · Texting의 ReceivingEnabled가 Foreground나 Always여야 합니다. 휴대폰 메시지 "
            "앱에도 들어갑니다.")
BATTERY_HINT = (f"충전 중이 아니면 {BATTERY_MIN_UNPLUGGED}%까지만 내려갑니다: 이 휴대폰은 20% 이하 경고를 띄우다 시스템 "
                "화면이 멈춥니다(시스템 이미지 문제). 더 낮은 값은 '충전 중'을 켜고 시험하세요.")
CAMERA_HINT = ("→ Camera.TakePicture · 휴대폰 카메라가 이 그림을 비춥니다. 바꾼 그림은 앱이 카메라를 다시 열 때 "
               "보입니다.")
CALL_HINT = ("→ PhoneCall.PhoneCallStarted · PhoneCallEnded · 받기(IncomingCallAnswered)와 거절은 휴대폰 화면에 뜬 "
             "전화 알림에서 합니다.")


def _caption(words: str) -> QLabel:
    label = QLabel(words)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet("color:#52606d")
    return label


class TiltPad(QWidget):
    """Drag the dot to tilt (up/down: forward/back, left/right: sideways), double-click to level it."""
    moved = Signal(float, float)   # pitch, roll in degrees

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(132, 132)
        self.setToolTip("끌어서 기울이기 · 더블클릭하면 수평")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._at = QPointF(0, 0)                    # -1..1 each, y up
        self._pending: tuple[float, float] | None = None
        self._sent: tuple[float, float] = (0.0, 0.0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TILT_EMIT_MS)
        self._timer.timeout.connect(self._flush)

    def degrees(self) -> tuple[float, float]:
        return round(self._at.y() * TILT_MAX_DEG, 1), round(-self._at.x() * TILT_MAX_DEG, 1)

    def center(self) -> None:
        """Level again without telling anyone (a turn levels the phone itself)."""
        self._at = QPointF(0, 0)
        self._pending, self._sent = None, (0.0, 0.0)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setPen(QPen(QColor(120, 130, 140), 1))
        p.drawRoundedRect(r, 10, 10)
        c = r.center()
        p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
        p.drawLine(QPointF(c.x(), r.top()), QPointF(c.x(), r.bottom()))
        dot = QPointF(c.x() + self._at.x() * r.width() / 2, c.y() - self._at.y() * r.height() / 2)
        p.setBrush(QColor(26, 115, 232) if self.isEnabled() else QColor(150, 150, 150))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(dot, 9, 9)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._move_to(event.position())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._move_to(event.position())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self.center()
        self._queue((0.0, 0.0))

    def _move_to(self, pos: QPointF) -> None:
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        x = (pos.x() - r.center().x()) / (r.width() / 2)
        y = (r.center().y() - pos.y()) / (r.height() / 2)
        self._at = QPointF(min(max(x, -1.0), 1.0), min(max(y, -1.0), 1.0))
        self.update()
        self._queue(self.degrees())

    def _queue(self, value: tuple[float, float]) -> None:
        if self._timer.isActive():
            self._pending = value
            return
        self._emit(value)

    def _flush(self) -> None:
        if self._pending is not None:
            value, self._pending = self._pending, None
            self._emit(value)

    def _emit(self, value: tuple[float, float]) -> None:
        if value != self._sent:
            self._sent = value
            self.moved.emit(*value)
            self._timer.start()


class SensorPanel(QScrollArea):
    shake = Signal()
    sensitivity_changed = Signal(object)   # Sensitivity, chosen by the user
    rotate = Signal(int)                   # +1 = turn left (counter-clockwise), -1 = right
    match = Signal()
    tilt = Signal(float, float)
    locate = Signal(float, float)
    microphone = Signal(bool)
    light = Signal(float)                  # lux
    proximity = Signal(float)              # cm
    battery = Signal(int, bool)            # level %, charging
    sms = Signal(str, str)                 # sender, text
    call = Signal(str, bool)               # number; True: it rings, False: hang up
    camera = Signal(bool)                  # [P4] True: pick a picture for the camera, False: the color bars again

    def __init__(self, presets: tuple[tuple[float, float, str], ...]) -> None:
        super().__init__()
        self._available = False
        self._host_mic = True
        self._mic_quiet = False
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.addWidget(self._shake_box())
        lay.addWidget(self._turn_box())
        lay.addWidget(self._tilt_box())
        lay.addWidget(self._location_box(presets))
        lay.addWidget(self._mic_box())
        lay.addWidget(self._world_box())
        lay.addWidget(self._phone_box())
        lay.addWidget(self._camera_box())
        lay.addStretch(1)
        self.setWidget(body)
        self.set_device(False)

    # --- sections ---------------------------------------------------------------------------------------------------
    def _shake_box(self) -> QGroupBox:
        box = QGroupBox("흔들기")
        self.shake_button = QPushButton("흔들기 (F6)")
        f = self.shake_button.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.shake_button.setFont(f)
        self.shake_button.setMinimumHeight(44)
        self.shake_button.setAutoRepeat(True)   # held: one pulse per 450 ms
        self.shake_button.setAutoRepeatDelay(SHAKE_REPEAT_MS)
        self.shake_button.setAutoRepeatInterval(SHAKE_REPEAT_MS)
        self.shake_button.clicked.connect(lambda _=False: self.shake.emit())
        self.sensitivity_box = QComboBox()
        for s in (Sensitivity.WEAK, Sensitivity.MODERATE, Sensitivity.STRONG):
            self.sensitivity_box.addItem(SENSITIVITY_NAMES[s], s)
        self.sensitivity_box.setCurrentIndex(1)
        self.sensitivity_box.activated.connect(lambda _i: self.sensitivity_changed.emit(self.sensitivity()))
        row = QHBoxLayout()
        row.addWidget(QLabel("감도"))
        row.addWidget(self.sensitivity_box, 1)
        lay = QVBoxLayout(box)
        lay.addWidget(self.shake_button)
        lay.addLayout(row)
        lay.addWidget(_caption("→ AccelerometerSensor.Shaking · 앱의 Sensitivity와 같은 감도를 고르세요(앱마다 기억)."))
        return box

    def _turn_box(self) -> QGroupBox:
        box = QGroupBox("회전")
        self.left_button = QPushButton("↺ 왼쪽 (F7)")
        self.right_button = QPushButton("오른쪽 (F8) ↻")
        self.left_button.clicked.connect(lambda _=False: self.rotate.emit(1))
        self.right_button.clicked.connect(lambda _=False: self.rotate.emit(-1))
        self.match_button = QPushButton("화면 방향 맞추기")
        self.match_button.setToolTip("앱 화면이 돌아가 보일 때(가로 고정 화면 등) 휴대폰을 앱 방향으로 돌립니다.")
        self.match_button.clicked.connect(lambda _=False: self.match.emit())
        self.posture_label = QLabel()
        row = QHBoxLayout()
        row.addWidget(self.left_button)
        row.addWidget(self.right_button)
        lay = QVBoxLayout(box)
        lay.addLayout(row)
        lay.addWidget(self.posture_label)
        lay.addWidget(self.match_button)
        lay.addWidget(_caption("→ Screen.ScreenOrientationChanged · 흔들기로 잡히지 않게 15°씩 0.6초에 걸쳐 돌립니다."))
        self.set_posture(Rotation.PORTRAIT)
        return box

    def _tilt_box(self) -> QGroupBox:
        box = QGroupBox("기울기")
        self.pad = TiltPad()
        self.pad.moved.connect(self._tilted)
        self.tilt_label = QLabel("수평")
        side = QVBoxLayout()
        side.addWidget(self.tilt_label)
        side.addWidget(_caption("→ AccelerometerSensor · OrientationSensor (최대 20°, 더블클릭하면 수평)"))
        side.addStretch(1)
        lay = QHBoxLayout(box)
        lay.addWidget(self.pad)
        lay.addLayout(side, 1)
        return box

    def _location_box(self, presets: tuple[tuple[float, float, str], ...]) -> QGroupBox:
        box = QGroupBox("위치")
        self.preset_box = QComboBox()
        for lat, lon, name in presets:
            self.preset_box.addItem(name, (lat, lon))
        self.preset_box.activated.connect(self._preset_chosen)
        self.lat = QDoubleSpinBox()
        self.lon = QDoubleSpinBox()
        for spin, limit in ((self.lat, 90.0), (self.lon, 180.0)):
            spin.setDecimals(6)
            spin.setRange(-limit, limit)
            spin.setSingleStep(0.001)
        send = QPushButton("보내기")
        send.clicked.connect(lambda _=False: self._send_location(None))
        self.location_label = QLabel()
        self.location_label.setTextFormat(Qt.TextFormat.PlainText)   # preset names come from settings.json
        grid = QGridLayout()
        grid.addWidget(QLabel("장소"), 0, 0)
        grid.addWidget(self.preset_box, 0, 1, 1, 2)
        grid.addWidget(QLabel("위도"), 1, 0)
        grid.addWidget(self.lat, 1, 1)
        grid.addWidget(QLabel("경도"), 2, 0)
        grid.addWidget(self.lon, 2, 1)
        grid.addWidget(send, 2, 2)
        self.location_buttons = [self.preset_box, self.lat, self.lon, send]
        lay = QVBoxLayout(box)
        lay.addLayout(grid)
        lay.addWidget(self.location_label)
        lay.addWidget(_caption("→ LocationSensor.LocationChanged"))
        for hint in LOCATION_HINTS:
            lay.addWidget(_caption(hint))
        if presets:
            self.reset_location()
        return box

    def _mic_box(self) -> QGroupBox:
        box = QGroupBox("마이크")
        self.mic_button = QPushButton("마이크 켜기")
        self.mic_button.setCheckable(True)
        self.mic_button.toggled.connect(self._mic_toggled)
        self.mic_note = _caption("")
        lay = QVBoxLayout(box)
        lay.addWidget(self.mic_button)
        lay.addWidget(self.mic_note)
        lay.addWidget(_caption("→ SoundRecorder · SpeechRecognizer"))
        lay.addWidget(_caption(MIC_HINT))
        return box

    def _world_box(self) -> QGroupBox:
        box = QGroupBox("빛·근접·배터리")
        self.light_spin = QSpinBox()
        self.light_spin.setRange(0, 100000)
        self.light_spin.setSingleStep(50)
        self.light_spin.setSuffix(" lux")
        self.light_spin.setKeyboardTracking(False)   # Enter or the arrows send, not every digit typed
        self.light_spin.valueChanged.connect(lambda v: self.light.emit(float(v)))
        self.light_presets = [QPushButton(name) for name, _ in LIGHT_PRESETS]
        for b, (_, lux) in zip(self.light_presets, LIGHT_PRESETS, strict=True):
            b.clicked.connect(lambda _=False, v=lux: self.light_spin.setValue(v))
        self.near_box = QCheckBox("가까이 (센서를 손으로 가림)")
        self.near_box.toggled.connect(lambda on: self.proximity.emit(0.0 if on else PROXIMITY_FAR_CM))
        self.battery_spin = QSpinBox()
        self.battery_spin.setRange(0, 100)
        self.battery_spin.setSuffix(" %")
        self.battery_spin.setKeyboardTracking(False)
        self.battery_spin.valueChanged.connect(lambda _v: self._battery_changed())
        self.charging_box = QCheckBox("충전 중")
        self.charging_box.toggled.connect(self._charging_toggled)
        grid = QGridLayout()
        grid.addWidget(QLabel("밝기"), 0, 0)
        grid.addWidget(self.light_spin, 0, 1)
        row = QHBoxLayout()
        for b in self.light_presets:
            row.addWidget(b)
        grid.addLayout(row, 1, 1)
        grid.addWidget(QLabel("근접"), 2, 0)
        grid.addWidget(self.near_box, 2, 1)
        grid.addWidget(QLabel("배터리"), 3, 0)
        battery = QHBoxLayout()
        battery.addWidget(self.battery_spin)
        battery.addWidget(self.charging_box, 1)
        grid.addLayout(battery, 3, 1)
        grid.setColumnStretch(1, 1)
        self.world_widgets: list[QWidget] = [self.light_spin, *self.light_presets, self.near_box, self.battery_spin,
                                             self.charging_box]
        lay = QVBoxLayout(box)
        lay.addLayout(grid)
        lay.addWidget(_caption("→ LightSensor.LightChanged · ProximitySensor.ProximityChanged (가까이 0 cm, 멀리 1 cm)"))
        lay.addWidget(_caption("배터리는 휴대폰 상태 표시줄과, 배터리를 읽는 확장 기능에 보입니다."))
        lay.addWidget(_caption(BATTERY_HINT))
        self.set_world(PhoneWorld())
        return box

    def _phone_box(self) -> QGroupBox:
        box = QGroupBox("문자·전화")
        self.number_edit = QLineEdit(DEFAULT_NUMBER)
        self.number_edit.setValidator(QRegularExpressionValidator(PHONE_NUMBER, self.number_edit))
        self.number_edit.setPlaceholderText("상대 번호")
        self.sms_edit = QLineEdit()
        self.sms_edit.setMaxLength(SMS_MAX)
        self.sms_edit.setPlaceholderText(f"문자 내용 ({SMS_MAX}자까지)")
        self.sms_button = QPushButton("문자 받기")
        self.call_button = QPushButton("전화 오기")
        self.hangup_button = QPushButton("끊기")
        self.sms_edit.returnPressed.connect(self._send_sms)
        self.sms_button.clicked.connect(lambda _=False: self._send_sms())
        self.call_button.clicked.connect(lambda _=False: self.call.emit(self.number_edit.text(), True))
        self.hangup_button.clicked.connect(lambda _=False: self.call.emit(self.number_edit.text(), False))
        for edit in (self.number_edit, self.sms_edit):
            edit.textChanged.connect(lambda _t: self._phone_buttons())
        grid = QGridLayout()
        grid.addWidget(QLabel("번호"), 0, 0)
        grid.addWidget(self.number_edit, 0, 1, 1, 2)
        grid.addWidget(QLabel("문자"), 1, 0)
        grid.addWidget(self.sms_edit, 1, 1)
        grid.addWidget(self.sms_button, 1, 2)
        calls = QHBoxLayout()
        calls.addWidget(self.call_button)
        calls.addWidget(self.hangup_button)
        grid.addLayout(calls, 2, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        lay = QVBoxLayout(box)
        lay.addLayout(grid)
        lay.addWidget(_caption(SMS_HINT))
        lay.addWidget(_caption(CALL_HINT))
        return box

    def _camera_box(self) -> QGroupBox:
        box = QGroupBox("카메라")
        pick = QPushButton("그림 고르기…")
        bars = QPushButton("기본 그림")
        pick.clicked.connect(lambda _=False: self.camera.emit(True))
        bars.clicked.connect(lambda _=False: self.camera.emit(False))
        self.camera_buttons = [pick, bars]
        self.camera_label = QLabel("지금: 기본 그림 (색 막대)")
        self.camera_label.setTextFormat(Qt.TextFormat.PlainText)   # a file name the user chose
        row = QHBoxLayout()
        row.addWidget(pick)
        row.addWidget(bars)
        lay = QVBoxLayout(box)
        lay.addLayout(row)
        lay.addWidget(self.camera_label)
        lay.addWidget(_caption(CAMERA_HINT))
        return box

    # --- state from the window ------------------------------------------------------------------------------------
    def set_device(self, available: bool) -> None:
        self._available = available
        for w in (self.shake_button, self.sensitivity_box, self.left_button, self.right_button, self.match_button,
                  self.pad, *self.location_buttons, *self.world_widgets, self.number_edit, self.sms_edit):
            w.setEnabled(available)
        self.mic_button.setEnabled(available and self._host_mic)
        self._phone_buttons()

    def set_world(self, world: PhoneWorld) -> None:
        """What the phone has now (a new phone: the golden's, or a kept phone's, §6.3); nothing is sent."""
        widgets = (self.light_spin, self.near_box, self.battery_spin, self.charging_box)
        blockers = [QSignalBlocker(w) for w in widgets]
        self.light_spin.setValue(round(world.light_lux))
        self.near_box.setChecked(world.proximity_cm < PROXIMITY_FAR_CM)
        self.charging_box.setChecked(world.charging)
        self._battery_floor()
        self.battery_spin.setValue(world.battery)
        for b in blockers:
            b.unblock()

    def sensitivity(self) -> Sensitivity:
        return Sensitivity(self.sensitivity_box.currentData())

    def set_sensitivity(self, sensitivity: Sensitivity) -> None:
        self.sensitivity_box.setCurrentIndex(max(0, self.sensitivity_box.findData(sensitivity)))

    def set_posture(self, rotation: Rotation) -> None:
        self.posture_label.setText(f"지금: {POSTURE_NAMES[rotation]}")

    def center_tilt(self) -> None:
        self.pad.center()
        self.tilt_label.setText("수평")

    def reset_location(self) -> None:
        """A new phone starts at the first preset (session_init puts Seoul City Hall there, §6.6)."""
        if self.preset_box.count():
            self.preset_box.setCurrentIndex(0)
            lat, lon = self.preset_box.itemData(0)
            self.lat.setValue(lat)
            self.lon.setValue(lon)
            self.location_label.setText(f"지금: {self.preset_box.itemText(0)} ({lat:.4f}, {lon:.4f})")

    def set_mic(self, on: bool, *, host_mic: bool, app_uses_mic: bool) -> None:
        self._host_mic = host_mic
        self._mic_quiet = True   # a state from the phone, not a click
        self.mic_button.setChecked(on and host_mic)
        self._mic_quiet = False
        self.mic_button.setText("마이크 끄기" if on and host_mic else "마이크 켜기")
        self.mic_button.setEnabled(host_mic and self._available)
        if not host_mic:
            note = "이 PC에 마이크가 없습니다. 녹음은 조용한 소리가 되고 음성 인식은 결과가 없습니다."
        elif on:
            note = "켜짐: PC 마이크 소리가 휴대폰으로 갑니다."
        elif app_uses_mic:
            note = "이 앱은 마이크를 씁니다. 켜면 PC 마이크로 녹음할 수 있습니다."
        else:
            note = "꺼짐: 휴대폰은 조용한 소리를 듣습니다."
        self.mic_note.setText(note)
        self.mic_note.setStyleSheet("color:#b3261e" if on and host_mic else
                                    "color:#174ea6" if app_uses_mic and host_mic else "color:#52606d")

    # --- internals ------------------------------------------------------------------------------------------------
    def _tilted(self, pitch: float, roll: float) -> None:
        self.tilt_label.setText("수평" if (pitch, roll) == (0.0, 0.0) else f"앞뒤 {pitch:+.0f}° · 좌우 {roll:+.0f}°")
        self.tilt.emit(pitch, roll)

    def _preset_chosen(self, index: int) -> None:
        lat, lon = self.preset_box.itemData(index)
        self.lat.setValue(lat)
        self.lon.setValue(lon)
        self._send_location(self.preset_box.itemText(index))

    def _send_location(self, name: str | None) -> None:
        lat, lon = self.lat.value(), self.lon.value()
        self.location_label.setText(f"지금: {name + ' ' if name else ''}({lat:.4f}, {lon:.4f})")
        self.locate.emit(lat, lon)

    def _mic_toggled(self, on: bool) -> None:
        if not self._mic_quiet:
            self.microphone.emit(on)

    def _battery_changed(self) -> None:
        self.battery.emit(self.battery_spin.value(), self.charging_box.isChecked())

    def _battery_floor(self) -> None:
        """Unplugged, the spin box stops at 21 % (the image's SystemUI, see device.controls)."""
        self.battery_spin.setMinimum(0 if self.charging_box.isChecked() else BATTERY_MIN_UNPLUGGED)

    def _charging_toggled(self, _on: bool) -> None:
        blocker = QSignalBlocker(self.battery_spin)   # the new floor may raise the level: one send for both
        self._battery_floor()
        blocker.unblock()
        self._battery_changed()

    def _phone_buttons(self) -> None:
        number = self.number_edit.hasAcceptableInput()
        self.call_button.setEnabled(self._available and number)
        self.hangup_button.setEnabled(self._available and number)
        self.sms_button.setEnabled(self._available and number and bool(self.sms_edit.text().strip()))

    def _send_sms(self) -> None:
        if self.sms_button.isEnabled():
            self.sms.emit(self.number_edit.text(), self.sms_edit.text().strip())
