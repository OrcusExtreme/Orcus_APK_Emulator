"""Shake pulses and gradual turns as pure waveforms (ARCHITECTURE.md §9.7); `device-controls` plays them.

App Inventor's AccelerometerSensor adds each sample to a 10-sample cache first, then fires Shaking when
|sample - cache mean| on any axis is inside the band of its sensitivity (Strong 5-13, Moderate 13-20, Weak > 20)
and 400 ms have passed since the last one (SENSOR_DELAY_GAME, a sample every 20 ms).
- A pulse of Δ along the gravity axis deviates 0.9Δ on its first sample (16 -> 14.4 for Moderate), inside the
  band of the matching sensitivity; the later samples fall inside the 400 ms.
- A turn in one step moves gravity by up to 19.6 m/s² and is caught as a shake (S7). 15° every 0.1 s keeps the
  deviation under 3.4 m/s² (below Strong's 5); the unit test plays it against the ported detector.
"""

import math
from collections.abc import Sequence

from orcus.contracts import Sensitivity

SETTLE_S, PULSE_S = 0.25, 0.06   # the baseline fills the 10-sample cache, then about 3 samples of pulse
DELTA = {Sensitivity.WEAK: 25.0, Sensitivity.MODERATE: 16.0, Sensitivity.STRONG: 9.0}
REPEAT_S = 0.45                  # a held button: longer than MinimumInterval's 400 ms
TURN_STEP_DEG, TURN_STEP_S = 15.0, 0.1
RESTING = (0.0, 9.81, 0.0)       # portrait at rest, when the emulator has no reading yet


def pulse_for(base: Sequence[float], delta: float) -> list[float]:
    """Push the gravity axis away from zero by delta (never across it: upside down, -9.81 becomes -9.81-Δ)."""
    axis = max(range(3), key=lambda i: abs(base[i]))
    out = list(base)
    out[axis] += delta if base[axis] >= 0 else -delta
    return out


def shake_steps(base: Sequence[float], sensitivity: Sensitivity) -> list[tuple[list[float], float]]:
    """(acceleration override, seconds to hold it). The caller then restores the posture with setPhysicalModel,
    which also clears the override."""
    b = list(base) if len(base) == 3 else list(RESTING)
    return [(b, SETTLE_S), (pulse_for(b, DELTA[sensitivity]), PULSE_S)]


def turn_steps(start: Sequence[float], end: Sequence[float]) -> list[list[float]]:
    """Rotation vectors after start up to end, no axis moving more than 15° per step; z goes the short way."""
    dz = (end[2] - start[2] + 180.0) % 360.0 - 180.0
    dx, dy = end[0] - start[0], end[1] - start[1]
    n = max(1, math.ceil(max(abs(dx), abs(dy), abs(dz)) / TURN_STEP_DEG - 1e-9))
    return [[start[0] + dx * i / n, start[1] + dy * i / n, (start[2] + dz * i / n) % 360.0]
            for i in range(1, n + 1)]
