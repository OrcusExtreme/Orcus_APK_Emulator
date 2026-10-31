"""device/shake.py against App Inventor's own shake detector, ported (ARCHITECTURE.md §9.7 "검증").

AccelerometerSensor.java (appinventor-sources master, 2026-10-29): AccelerationChanged adds the sample to a
10-sample cache per axis first, then fires Shaking when |cache mean - sample| on any axis is inside the band
of the sensitivity and MinimumInterval (400 ms) has passed. SENSOR_DELAY_GAME gives a sample every 20 ms.
The model is checked against S7 first: one-step turns and the cross-sensitivity table behave as measured.
"""

import math
from collections import deque
from collections.abc import Callable, Sequence

import pytest

from orcus.contracts import Rotation, Sensitivity
from orcus.device import shake
from orcus.device.rotation import Z_DEGREES

SAMPLE_MS = 20
G = 9.81
REST_MS = 1000

type Wave = list[tuple[Sequence[float], float]]   # (acceleration, milliseconds held)


class Ai2Detector:
    """AccelerometerSensor.AccelerationChanged + isShaking, as in the Java source."""

    def __init__(self, sensitivity: Sensitivity, min_interval_ms: int = 400) -> None:
        self.caches: list[deque[float]] = [deque(maxlen=10) for _ in range(3)]   # SENSOR_CACHE_SIZE
        self.sensitivity = sensitivity
        self.min_interval = min_interval_ms
        self.last = 0.0
        self.fired: list[float] = []

    def _shaking(self, cache: deque[float], value: float) -> bool:
        delta = abs(sum(cache) / len(cache) - value)
        match self.sensitivity:
            case Sensitivity.WEAK:
                return delta > 20.0                  # > strongShakeThreshold
            case Sensitivity.MODERATE:
                return 13.0 < delta < 20.0
            case Sensitivity.STRONG:
                return 5.0 < delta < 13.0

    def sample(self, t_ms: float, accel: Sequence[float]) -> None:
        for cache, v in zip(self.caches, accel):
            cache.append(v)
        if any(self._shaking(c, v) for c, v in zip(self.caches, accel)) \
                and (self.last == 0 or t_ms >= self.last + self.min_interval):
            self.last = t_ms
            self.fired.append(t_ms)


def gravity(z_deg: float) -> list[float]:
    """Resting reading with the screen turned by z (PORTRAIT reads (0, g, 0); upside down y is -g)."""
    z = math.radians(z_deg)
    return [G * math.sin(z), G * math.cos(z), 0.0]


def play(wave: Wave, sensitivity: Sensitivity, phase_ms: float) -> int:
    """Sample the piecewise-constant wave every 20 ms from `phase_ms`; Shaking events counted."""
    det = Ai2Detector(sensitivity)
    edges: list[tuple[float, Sequence[float]]] = []
    t = 10_000.0   # System.currentTimeMillis() is never near 0
    for accel, ms in wave:
        edges.append((t, accel))
        t += ms
    end = t
    s = edges[0][0] + phase_ms
    while s < end:
        accel = next(a for start, a in reversed(edges) if start <= s)
        det.sample(s, accel)
        s += SAMPLE_MS
    return len(det.fired)


def shake_wave(z_deg: float, sensitivity: Sensitivity, pulse_ms: float) -> Wave:
    rest = gravity(z_deg)
    (base, settle_s), (pulse, _) = shake.shake_steps(rest, sensitivity)
    return [(rest, REST_MS), (base, settle_s * 1000), (pulse, pulse_ms), (rest, REST_MS)]


def turn_wave(z_from: float, z_to: float, step: Callable[[float, float], list[list[float]]]) -> Wave:
    wave: Wave = [(gravity(z_from), REST_MS)]
    for rot in step(z_from, z_to):
        wave.append((gravity(rot[2]), shake.TURN_STEP_S * 1000))
    wave.append((gravity(z_to), REST_MS))
    return wave


def gradual(z_from: float, z_to: float) -> list[list[float]]:
    return shake.turn_steps([0.0, 0.0, z_from], [0.0, 0.0, z_to])


def one_step(_z_from: float, z_to: float) -> list[list[float]]:
    return [[0.0, 0.0, z_to]]


PHASES = range(0, SAMPLE_MS, 3)
POSTURES = [Z_DEGREES[r] for r in Rotation]


@pytest.mark.parametrize("sensitivity", list(Sensitivity))
@pytest.mark.parametrize("z", POSTURES)
def test_matching_sensitivity_fires_exactly_once(sensitivity: Sensitivity, z: float) -> None:
    """B3: one click = one Shaking, in all four postures, whatever the sample phase and a pulse of 40-100 ms
    (the 60 ms hold plus gRPC jitter)."""
    for pulse_ms in (40, 50, 60, 70, 80, 100):
        for phase in PHASES:
            assert play(shake_wave(z, sensitivity, pulse_ms), sensitivity, phase) == 1, (pulse_ms, phase)


@pytest.mark.parametrize(("listening", "delta", "count"), [
    (Sensitivity.WEAK, 25, 1), (Sensitivity.MODERATE, 16, 1), (Sensitivity.STRONG, 9, 1),
    (Sensitivity.MODERATE, 25, 1), (Sensitivity.STRONG, 16, 1),          # a bigger pulse also rings
    (Sensitivity.MODERATE, 9, 0), (Sensitivity.WEAK, 16, 0),             # a smaller one does not
])
def test_the_model_matches_s7(listening: Sensitivity, delta: float, count: int) -> None:
    """S7 on the device (OrcusProbe, portrait): the ported detector with the 60 ms pulse gives the same."""
    rest = gravity(0)
    wave: Wave = [(rest, REST_MS), (rest, 250), (shake.pulse_for(rest, delta), 60), (rest, REST_MS)]
    for phase in PHASES:
        assert play(wave, listening, phase) == count, phase


@pytest.mark.parametrize(("z_from", "z_to", "caught_by"), [
    (0, 90, {Sensitivity.STRONG}), (0, 180, {Sensitivity.STRONG, Sensitivity.MODERATE}),
])
def test_one_step_turns_are_caught_as_in_s7(z_from: float, z_to: float, caught_by: set[Sensitivity]) -> None:
    """S7: a 180° turn rang Moderate and Strong, a 90° turn rang Strong (STEP and SMOOTH alike)."""
    for sensitivity in Sensitivity:
        counts = {play(turn_wave(z_from, z_to, one_step), sensitivity, p) for p in PHASES}
        assert counts == ({1} if sensitivity in caught_by else {0}), sensitivity


@pytest.mark.parametrize(("z_from", "z_to"), [(0, 90), (90, 0), (0, 270), (270, 0), (90, 180), (180, 270),
                                              (0, 180), (90, 270)])
def test_gradual_turns_are_never_a_shake(z_from: float, z_to: float) -> None:
    """15° every 0.1 s: under Strong's 5 m/s² at every sample, so F7/F8 and [Match orientation] do not ring."""
    for sensitivity in Sensitivity:
        for phase in PHASES:
            assert play(turn_wave(z_from, z_to, gradual), sensitivity, phase) == 0, (sensitivity, phase)


def test_turn_steps_take_the_short_way_and_end_exactly() -> None:
    steps = shake.turn_steps([0.0, 0.0, 270.0], [0.0, 0.0, 0.0])
    assert [round(s[2], 6) for s in steps] == [285.0, 300.0, 315.0, 330.0, 345.0, 0.0]
    assert len(shake.turn_steps([0.0, 0.0, 0.0], [0.0, 0.0, 180.0])) == 12
    tilted = shake.turn_steps([20.0, 0.0, 85.0], [0.0, 0.0, 180.0])   # tilt and roll unwind on the way
    assert len(tilted) == 7 and tilted[-1] == [0.0, 0.0, 180.0]
    assert shake.turn_steps([0.0, 0.0, 90.0], [0.0, 0.0, 90.0]) == [[0.0, 0.0, 90.0]]


def test_pulse_never_crosses_zero_and_a_missing_reading_rests_upright() -> None:
    assert [round(v, 2) for v in shake.pulse_for([0.0, 9.81, 0.3], 16)] == [0.0, 25.81, 0.3]
    assert [round(v, 2) for v in shake.pulse_for([-9.81, 0.1, 0.0], 9)] == [-18.81, 0.1, 0.0]
    (base, settle), (pulse, hold) = shake.shake_steps([], Sensitivity.MODERATE)
    assert base == [0.0, 9.81, 0.0] and (settle, hold) == (0.25, 0.06)
    assert [round(v, 2) for v in pulse] == [0.0, 25.81, 0.0]
