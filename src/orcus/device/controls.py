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
The posture and the world (light, proximity, battery) are read from the phone when the epoch starts: a kept
phone (§6.3) comes back turned, lit and charged the way it was saved, and a turn from an assumed portrait would
jump. Light and proximity are physical models of their own; a text and a call go through the modem.
The battery stays above 20 % while not charging: at the 20 % warning the image's SystemUI asks Extreme Battery
Saver's provider (com.google.android.flipendo.api), which this image lacks, and crashes; the app in front pauses and
resumes, and crash after crash could wake the rescue party. Charging, any level is fine.
"""

import dataclasses
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import grpc
from google.protobuf import empty_pb2

from orcus.common import log
from orcus.contracts import BATTERY_MIN_UNPLUGGED, OrcusFailure, PhoneWorld, Rotation, Sensitivity
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
        self._posture = Rotation.PORTRAIT   # the golden's, until _sync() reads the phone
        self._world = PhoneWorld()
        self._mic = False                   # SESSION_INIT turns the microphone off (§6.6)
        self._resend: tuple[float, tuple[float, float, float], int] | None = None   # (due, location, sends left)
        self._sync()
        self._thread = threading.Thread(target=self._run, name="device-controls", daemon=True)
        self._thread.start()

    @property
    def posture(self) -> Rotation:
        return self._posture

    @property
    def microphone(self) -> bool:
        return self._mic

    @property
    def world(self) -> PhoneWorld:
        return self._world

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

    def set_light(self, lux: float) -> None:
        self._world = dataclasses.replace(self._world, light_lux=max(0.0, lux))
        self._q.put(("model", (ec.PhysicalModelValue.LIGHT, self._world.light_lux)))

    def set_proximity(self, cm: float) -> None:
        self._world = dataclasses.replace(self._world, proximity_cm=max(0.0, cm))
        self._q.put(("model", (ec.PhysicalModelValue.PROXIMITY, self._world.proximity_cm)))

    def set_battery(self, level: int, charging: bool) -> None:
        level = min(max(level, 0 if charging else BATTERY_MIN_UNPLUGGED), 100)
        self._world = dataclasses.replace(self._world, battery=level, charging=charging)
        self._q.put(("battery", (self._world.battery, charging)))

    def receive_sms(self, sender: str, text: str) -> None:
        self._q.put(("sms", (sender, text)))

    def call_in(self, number: str) -> None:
        self._q.put(("phone", (ec.PhoneCall.InitCall, number)))

    def call_end(self, number: str) -> None:
        self._q.put(("phone", (ec.PhoneCall.DisconnectCall, number)))

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

    def _sync(self) -> None:
        """In the constructor (SESSION_INIT): four quick reads; on failure the golden's values stay."""
        P, B = ec.PhysicalModelValue, ec.BatteryState   # noqa: N806
        try:
            rot = list(self._stub.getPhysicalModel(P(target=P.ROTATION), timeout=5).value.data)
            light = list(self._stub.getPhysicalModel(P(target=P.LIGHT), timeout=5).value.data)
            near = list(self._stub.getPhysicalModel(P(target=P.PROXIMITY), timeout=5).value.data)
            battery = self._stub.getBattery(empty_pb2.Empty(), timeout=5)
        except grpc.RpcError as e:
            _log.warning("reading the phone's posture failed, the golden's assumed: %s", e.code())
            return
        if len(rot) == 3:   # the golden: [-4.75, 0, 0], the emulator's resting pose
            self._rot = rot
            self._posture = min(Z_DEGREES, key=lambda r: abs((Z_DEGREES[r] - rot[2] + 180.0) % 360.0 - 180.0))
            self._z0 = Z_DEGREES[self._posture]
        self._world = PhoneWorld(light[0] if light else 0.0, near[0] if near else 1.0, battery.chargeLevel,
                                 battery.charger != B.NONE)
        if self._posture != Rotation.PORTRAIT or self._world != PhoneWorld():
            _log.info("the phone starts %s, %s", self._posture.name, self._world)

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
            case "model":   # light and proximity: physical models of their own, the posture is not touched
                target, value = payload
                self._stub.setPhysicalModel(ec.PhysicalModelValue(
                    target=target, value=ec.ParameterValue(data=[value]),
                    interpolation=ec.PhysicalModelValue.STEP), timeout=5)
            case "battery":
                level, charging = payload
                B = ec.BatteryState   # noqa: N806
                self._stub.setBattery(B(hasBattery=True, isPresent=True, charger=B.AC if charging else B.NONE,
                                        chargeLevel=level, health=B.GOOD,
                                        status=B.CHARGING if charging else B.DISCHARGING), timeout=5)
            case "sms":   # Google Messages takes it; App Inventor's Texting gets MessageReceived
                sender, text = payload
                r = self._stub.sendSms(ec.SmsMessage(srcAddress=sender, text=text), timeout=10)
                if r.response != ec.PhoneResponse.OK:
                    _log.warning("sendSms: response %s", r.response)
            case "phone":
                operation, number = payload
                r = self._stub.sendPhone(ec.PhoneCall(operation=operation, number=number), timeout=10)
                if r.response != ec.PhoneResponse.OK:
                    _log.warning("sendPhone %s: response %s", operation, r.response)
            case _:
                pass
