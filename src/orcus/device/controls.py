"""Sensor commands on the `device-controls` FIFO thread (ARCHITECTURE.md §9.7, §9.8).

The thread owns the posture (rotation z, tilt x): setPhysicalModel clears every setSensor override, so a shake
pulse ends by restoring the posture with STEP. A shake (≈310 ms) and a gradual turn (15° per 0.1 s) run start
to end without interleaving; queued tilt updates merge into the newest.
S7 confirmed: Weak/Moderate/Strong pulses of 25/16/9 m/s² fire Shaking exactly once at their sensitivity, in
all four postures (the pulse pushes the gravity axis away from zero).
Locations go through the framework's test providers (S7), which needs a shell: the app injects that. A new
location is sent 3 times, 1 s apart (the app got it within seconds that way in S7); a newer one replaces it.
Test providers report only when told, so an app that asks for updates later hears nothing until the next send:
resend_location() repeats the newest one (the session's start place until the user picks another).
"""

import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import grpc

from orcus.common import log
from orcus.contracts import OrcusFailure, Rotation, Sensitivity
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

from . import shake as waves
from .rotation import Z_DEGREES

_log = log.get("device.controls")
TILT_MAX_DEG = 20.0   # 26° flips the frame back to PORTRAIT in landscape (S7)
LOCATION_SENDS, LOCATION_GAP_S = 3, 1.0

type LocationSink = Callable[[float, float, float], None]


class ControlQueue:
    """DeviceControls. Every method only enqueues and returns."""

    def __init__(self, stub: Any, location: LocationSink | None = None,
                 home: tuple[float, float] | None = None) -> None:
        self._stub = stub
        self._location = location
        self._where: tuple[float, float, float] | None = (home[0], home[1], 30.0) if home else None
        self._q: "queue.Queue[tuple[str, Any] | None]" = queue.Queue()
        self._rot = [0.0, 0.0, 0.0]   # [x tilt, y (no effect on gravity, S7), z screen rotation]
        self._z0 = 0.0
        self._posture = Rotation.PORTRAIT   # a new epoch starts in the golden's portrait posture
        self._mic = False                   # SESSION_INIT turns the microphone off (§6.6)
        self._resend: tuple[float, tuple[float, float, float], int] | None = None   # (due, location, sends left)
        self._thread = threading.Thread(target=self._run, name="device-controls", daemon=True)
        self._thread.start()

    @property
    def posture(self) -> Rotation:
        return self._posture

    @property
    def microphone(self) -> bool:
        return self._mic

    def shake(self, sensitivity: Sensitivity) -> None:
        self._q.put(("shake", sensitivity))

    def rotate(self, to: Rotation, *, gradual: bool = True) -> None:
        self._posture = to
        self._q.put(("rotate", (to, gradual)))

    def tilt(self, pitch_deg: float, roll_deg: float) -> None:
        self._q.put(("tilt", (pitch_deg, roll_deg)))

    def set_location(self, lat: float, lon: float, alt_m: float = 30.0) -> None:
        self._where = (lat, lon, alt_m)
        self._q.put(("location", self._where))

    def resend_location(self) -> None:
        if self._where is not None:
            self._q.put(("location", self._where))

    def set_microphone(self, enabled: bool) -> None:
        self._mic = enabled
        self._q.put(("mic", enabled))

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(3)

    def _run(self) -> None:
        while True:
            wait = None if self._resend is None else max(0.0, self._resend[0] - time.monotonic())
            try:
                item = self._q.get(timeout=wait)
            except queue.Empty:
                assert self._resend is not None
                _, where, left = self._resend
                self._resend = (time.monotonic() + LOCATION_GAP_S, where, left - 1) if left > 1 else None
                self._safely("location", where)
                continue
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
            elif kind == "location":   # the newest location is the one to repeat
                self._resend = (time.monotonic() + LOCATION_GAP_S, payload, LOCATION_SENDS - 1)
            self._safely(kind, payload)

    def _safely(self, kind: str, payload: Any) -> None:
        try:
            self._do(kind, payload)
        except grpc.RpcError as e:
            _log.warning("%s failed: %s", kind, e.code())
        except OrcusFailure as e:   # an injected sink's failure, said by it (the phone resetting, say)
            _log.warning("%s failed: %s", kind, e)
        except Exception:
            _log.exception("%s failed", kind)

    def _set_rotation(self) -> None:
        self._stub.setPhysicalModel(ec.PhysicalModelValue(
            target=ec.PhysicalModelValue.ROTATION, value=ec.ParameterValue(data=self._rot),
            interpolation=ec.PhysicalModelValue.STEP), timeout=5)

    def _put_acceleration(self, v: list[float]) -> None:
        self._stub.setSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION, value=ec.ParameterValue(data=v)),
                             timeout=5)

    def _do(self, kind: str, payload: Any) -> None:
        match kind:
            case "shake":
                cur = self._stub.getSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION), timeout=5)
                for accel, hold_s in waves.shake_steps(list(cur.value.data), payload):
                    self._put_acceleration(accel)
                    time.sleep(hold_s)
                self._set_rotation()   # also clears the override
            case "rotate":
                to, gradual = payload
                end = [0.0, 0.0, Z_DEGREES[to]]
                for rot in waves.turn_steps(self._rot, end) if gradual else [end]:
                    if gradual:
                        time.sleep(waves.TURN_STEP_S)   # also after the previous command's last step
                    self._rot = rot
                    self._set_rotation()
                self._rot, self._z0 = end, end[2]
            case "tilt":
                pitch, roll = payload
                clamp = lambda d: max(-TILT_MAX_DEG, min(TILT_MAX_DEG, d))   # noqa: E731
                self._rot = [clamp(pitch), 0.0, (self._z0 + clamp(roll)) % 360.0]
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
