"""Frame-normalized point -> unrotated panel pixels (ARCHITECTURE.md §9.2, confirmed in S7 to 0-1 px).

MouseEvents are in panel coordinates before any rotation; the guest applies its own display rotation, so the
mapping depends only on the rotation of the frame the user clicked on (the device posture).
"""

from orcus.contracts import Rotation


def to_panel(rotation: Rotation, u: float, v: float, width: int, height: int) -> tuple[int, int]:
    u = min(max(u, 0.0), 1.0)
    v = min(max(v, 0.0), 1.0)
    match rotation:
        case Rotation.PORTRAIT:
            x, y = u * width, v * height
        case Rotation.LANDSCAPE:
            x, y = (1 - v) * width, u * height
        case Rotation.REVERSE_PORTRAIT:
            x, y = (1 - u) * width, (1 - v) * height
        case Rotation.REVERSE_LANDSCAPE:
            x, y = v * width, (1 - u) * height
    return min(round(x), width - 1), min(round(y), height - 1)


Z_DEGREES = {Rotation.PORTRAIT: 0.0, Rotation.LANDSCAPE: 90.0, Rotation.REVERSE_PORTRAIT: 180.0,
             Rotation.REVERSE_LANDSCAPE: 270.0}
