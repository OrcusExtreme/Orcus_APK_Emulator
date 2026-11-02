"""Device surface: frames, input, sensors, logcat (implemented by orcus.device over emulator gRPC)."""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Protocol

from .errors import OrcusError


class Rotation(IntEnum):
    PORTRAIT = 0
    LANDSCAPE = 1
    REVERSE_PORTRAIT = 2
    REVERSE_LANDSCAPE = 3


class HwKey(StrEnum):
    BACK = "GoBack"
    HOME = "GoHome"
    RECENTS = "AppSwitch"
    POWER = "Power"
    VOL_UP = "AudioVolumeUp"
    VOL_DOWN = "AudioVolumeDown"


class Sensitivity(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class Frame:
    """RGB888, rows top to bottom (confirmed in S2), stride = width * 3."""

    rgb888: bytes
    width: int
    height: int
    rotation: Rotation
    seq: int
    ts_us: int


@dataclass(frozen=True, slots=True)
class PointerAt:
    """Normalized position inside the displayed frame plus that frame's seq.

    Panel-pixel conversion (rotation, panel size) belongs to orcus.device.
    """

    u: float
    v: float
    frame_seq: int


@dataclass(frozen=True)
class LogEntry:
    ts_us: int
    pid: int
    tid: int
    level: str
    tag: str
    msg: str


class CancellableIter[T](Protocol):
    """Normal end and cancel raise StopIteration; failures raise OrcusFailure."""

    def __iter__(self) -> Iterator[T]: ...
    def cancel(self) -> None: ...


class FrameSource(Protocol):
    def start(self, width: int, height: int, on_frame: Callable[[Frame], None],
              on_end: Callable[[OrcusError | None], None]) -> None: ...
    def retarget(self, width: int, height: int) -> None: ...   # cancel the stream and request again
    def stop(self) -> None: ...


class InputSink(Protocol):
    """Every call only enqueues and returns."""

    def pointer(self, at: PointerAt, pressed: bool) -> None: ...
    def touches(self, points: Sequence[PointerAt]) -> None: ...
    # fingers 0..n-1 down at these points (a pinch); fingers no longer listed lift, () lifts them all
    def wheel(self, at: PointerAt, dy: int) -> None: ...   # Qt angleDelta: 120 a notch, + scrolls up
    def key(self, key: HwKey | str, *, long_press: bool = False) -> None: ...   # always a keypress
    def chord(self, modifier: str, key: str) -> None: ...                      # e.g. ("Control", "a")
    def type_ascii(self, text: str) -> None: ...
    def paste_text(self, text: str) -> None: ...    # setClipboard -> 100 ms -> Paste, in order
    def release_all(self) -> None: ...


BATTERY_MIN_UNPLUGGED = 21   # 20 % and below, not charging, crashes the image's SystemUI (device.controls)


@dataclass(frozen=True, slots=True)
class PhoneWorld:
    """What the phone senses around it (§9.7). The golden is dark (0 lux), with nothing near (1 cm is the
    Goldfish sensor's far value) and full on AC; a kept phone (§6.3) comes back as it was saved."""
    light_lux: float = 0.0
    proximity_cm: float = 1.0
    battery: int = 100
    charging: bool = True


class DeviceControls(Protocol):
    """Everything goes to the device-controls FIFO thread and returns at once."""

    @property
    def posture(self) -> Rotation: ...      # the newest rotate() target; read from the phone when the epoch starts
    # (the golden is PORTRAIT; a kept phone comes back turned the way it was saved)
    @property
    def world(self) -> PhoneWorld: ...      # the newest set_light/proximity/battery; read at the start too
    @property
    def microphone(self) -> bool: ...       # the newest set_microphone(); SESSION_INIT turns it off
    def shake(self, sensitivity: Sensitivity) -> None: ...
    def rotate(self, to: Rotation, *, gradual: bool = True) -> None: ...
    # gradual: 15° per 0.1 s, or a running app catches the turn as a shake (§9.7). Before a launch nothing
    # listens yet, so pre-rotation turns in one step.
    def tilt(self, pitch_deg: float, roll_deg: float) -> None: ...
    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None: ...
    def resend_location(self) -> None: ...
    # the newest location again: test providers report only when told, so a LocationSensor that starts listening
    # later gets its first fix only from the next send
    def set_microphone(self, enabled: bool) -> None: ...
    # more of the phone's world (§9.7): light (lux), proximity (cm: 0 near), battery, a text, a call
    def set_light(self, lux: float) -> None: ...
    def set_proximity(self, cm: float) -> None: ...
    def set_battery(self, level: int, charging: bool) -> None: ...
    def receive_sms(self, sender: str, text: str) -> None: ...
    def call_in(self, number: str) -> None: ...
    def call_end(self, number: str) -> None: ...


class DeviceSession(Protocol):
    epoch: int
    frames: FrameSource
    input: InputSink
    controls: DeviceControls | None   # None for backends without sensors

    def logcat(self) -> CancellableIter[LogEntry]: ...
    def screenshot_png(self, on_done: Callable[[bytes | None], None]) -> None: ...   # non-blocking (device-io)
    def close(self) -> None: ...
    # close: release_all -> cancel streams -> close channel (engine calls it in SESSION_INIT, STOPPING, FAILED)
