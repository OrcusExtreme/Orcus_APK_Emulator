"""Sensor commands on the `device-controls` FIFO thread (ARCHITECTURE.md §9.7). Mostly [P3b]; the queue is 3a.

The thread owns the posture (rotation z, tilt x): setPhysicalModel clears every setSensor override, so a shake
pulse ends by restoring the posture with STEP. A shake runs start to end without interleaving (≈310 ms).
S7 confirmed: Weak/Moderate/Strong pulses of 25/16/9 m/s² fire Shaking exactly once at their sensitivity, in
all four postures (the pulse pushes the gravity axis away from zero).
Locations go through the framework's test providers (S7), which needs a shell: the app injects that.
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import grpc

from orcus.common import log
from orcus.contracts import Rotation, Sensitivity
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from .rotation import Z_DEGREES

_log = log.get("device.controls")
SHAKE_DELTA = {Sensitivity.WEAK: 25.0, Sensitivity.MODERATE: 16.0, Sensitivity.STRONG: 9.0}
SHAKE_SETTLE_S, SHAKE_PULSE_S = 0.25, 0.06
TILT_MAX_DEG = 20.0   # 26° flips the frame back to PORTRAIT in landscape (S7)

type LocationSink = Callable[[float, float, float], None]


def pulse_for(base: list[float], delta: float) -> list[float]:
    """Push the gravity axis away from zero by delta (never across it), §9.7."""
    axis = max(range(3), key=lambda i: abs(base[i]))
    out = list(base)
    out[axis] += delta if base[axis] >= 0 else -delta
    return out


class ControlQueue:
    """DeviceControls. Every method only enqueues and returns."""

    def __init__(self, stub: Any, location: LocationSink | None = None) -> None:
        self._stub = stub
        self._location = location
        self._q: "queue.Queue[tuple[str, Any] | None]" = queue.Queue()
        self._rot = [0.0, 0.0, 0.0]   # [x tilt, y (no effect on gravity, S7), z screen rotation]
        self._z0 = 0.0
        self._thread = threading.Thread(target=self._run, name="device-controls", daemon=True)
        self._thread.start()

    def shake(self, sensitivity: Sensitivity) -> None:
        self._q.put(("shake", sensitivity))

    def rotate(self, to: Rotation) -> None:
        self._q.put(("rotate", to))

    def tilt(self, pitch_deg: float, roll_deg: float) -> None:
        self._q.put(("tilt", (pitch_deg, roll_deg)))

    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None:
        self._q.put(("location", (lat, lon, alt_m)))

    def set_microphone(self, enabled: bool) -> None:
        self._q.put(("mic", enabled))

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(3)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            kind, payload = item
            if kind == "tilt":   # merge queued tilt updates: only the newest matters
                while True:
                    try:
                        nxt = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is not None and nxt[0] == "tilt":
                        payload = nxt[1]
                        continue
                    self._q.put(nxt)   # keep order for anything else
                    break
            try:
                self._do(kind, payload)
            except grpc.RpcError as e:
                _log.warning("%s failed: %s", kind, e.code())
            except Exception:
                _log.exception("%s failed", kind)

    def _set_rotation(self) -> None:
        self._stub.setPhysicalModel(ec.PhysicalModelValue(
            target=ec.PhysicalModelValue.ROTATION, value=ec.ParameterValue(data=self._rot),
            interpolation=ec.PhysicalModelValue.STEP), timeout=5)

    def _do(self, kind: str, payload: Any) -> None:
        match kind:
            case "shake":
                cur = self._stub.getSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION), timeout=5)
                base = list(cur.value.data) or [0.0, 9.81, 0.0]

                def put(v: list[float]) -> None:
                    self._stub.setSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION,
                                                        value=ec.ParameterValue(data=v)), timeout=5)
                put(base)
                time.sleep(SHAKE_SETTLE_S)
                put(pulse_for(base, SHAKE_DELTA[payload]))
                time.sleep(SHAKE_PULSE_S)
                self._set_rotation()   # also clears the override
            case "rotate":
                self._z0 = Z_DEGREES[payload]
                self._rot = [0.0, 0.0, self._z0]
                self._set_rotation()
            case "tilt":
                pitch, roll = payload
                clamp = lambda d: max(-TILT_MAX_DEG, min(TILT_MAX_DEG, d))   # noqa: E731
                self._rot = [clamp(pitch), 0.0, self._z0 + clamp(roll)]
                self._set_rotation()
            case "location":
                if self._location is None:
                    _log.warning("no location sink")
                else:
                    self._location(*payload)
            case "mic":
                self._stub.setMicrophoneState(ec.MicrophoneState(realAudioEnabled=bool(payload)), timeout=5)
            case _:
                pass
