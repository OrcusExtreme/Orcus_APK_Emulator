"""Helpers shared by the fixture spikes (throwaway, Phase 2): UI dumps, taps, keys, paste, logcat and audio taps.

Taps and keys go through gRPC like the product will (MouseEvent / KeyboardEvent); adb is only for reading.
"""

import array
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from apkInspector.axml import parse_apk_for_manifest
from harness import Live, say

from orcus.emugrpc.generated import emulator_controller_pb2 as ec

FIXTURES = Path(r"D:\OrcusDev\fixtures")
OUT = Path(r"D:\OrcusDev\tmp\fx")
OUT.mkdir(parents=True, exist_ok=True)
__all__ = ["Live", "say"]


def package_of(apk: Path) -> str:
    m = re.search(r'<manifest[^>]*package="([^"]+)"', parse_apk_for_manifest(str(apk)))
    assert m, apk
    return m.group(1)


# --- UI ----------------------------------------------------------------------------------------------------
@dataclass
class Node:
    text: str
    cls: str
    rid: str
    desc: str
    pkg: str
    bounds: tuple[int, int, int, int]
    password: bool
    focused: bool

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def size(self) -> tuple[int, int]:
        return self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1]


@dataclass
class Dump:
    rotation: int
    nodes: list[Node]
    attempts: int
    seconds: float

    def label(self, word: str) -> str | None:
        """Text of the first node whose text is `word` or starts with `word `."""
        for n in self.nodes:
            if n.text == word or n.text.startswith(word + " "):
                return n.text
        return None

    def find(self, text: str | None = None, cls: str | None = None, rid_suffix: str | None = None) -> Node | None:
        for n in self.nodes:
            if (text is None or n.text == text) and (cls is None or n.cls == cls) and \
                    (rid_suffix is None or n.rid.endswith(rid_suffix)):
                return n
        return None

    def texts(self) -> list[str]:
        return [n.text for n in self.nodes if n.text]


def dump(live: Live, quiet_pkg: str | None = None, tries: int = 3) -> Dump:
    """uiautomator dump. If the UI never goes idle (sensor labels updating), make the app's uid idle for the dump."""
    t0 = time.monotonic()
    quieted = False
    try:
        for attempt in range(1, tries + 1):
            r = live.adb("shell", "uiautomator dump /sdcard/ui.xml", timeout_s=45)
            if "dumped to" in r.out + r.err:
                xml = live.adb("exec-out", "cat /sdcard/ui.xml", timeout_s=30).out
                root = ET.fromstring(xml[xml.index("<"):])
                nodes = []
                for e in root.iter("node"):
                    b = [int(v) for v in re.findall(r"-?\d+", e.get("bounds", "[0,0][0,0]"))]
                    nodes.append(Node(e.get("text", ""), e.get("class", ""), e.get("resource-id", ""),
                                      e.get("content-desc", ""), e.get("package", ""), (b[0], b[1], b[2], b[3]),
                                      e.get("password") == "true", e.get("focused") == "true"))
                return Dump(int(root.get("rotation", "0")), nodes, attempt, round(time.monotonic() - t0, 1))
            say("dump failed:", (r.out + r.err).strip()[:120])
            if quiet_pkg and not quieted:
                live.shell("cmd", "sensorservice", "set-uid-state", quiet_pkg, "idle")
                quieted = True
        raise RuntimeError("uiautomator dump failed")
    finally:
        if quieted:
            live.shell("cmd", "sensorservice", "set-uid-state", quiet_pkg or "", "reset")


# --- input -------------------------------------------------------------------------------------------------
def tap(live: Live, x: int, y: int, hold_s: float = 0.06) -> None:
    live.stub.sendMouse(ec.MouseEvent(x=int(x), y=int(y), buttons=1), timeout=5)
    time.sleep(hold_s)
    live.stub.sendMouse(ec.MouseEvent(x=int(x), y=int(y), buttons=0), timeout=5)


def touch_tap(live: Live, x: int, y: int, hold_s: float = 0.06) -> None:
    """A finger tap (TouchEvent, pressure > 0 then 0) instead of a MouseEvent."""
    def ev(pressure: int) -> ec.InputEvent:
        return ec.InputEvent(touch_event=ec.TouchEvent(touches=[ec.Touch(x=int(x), y=int(y), identifier=0,
                                                                          pressure=pressure)]))

    def events():
        yield ev(1)
        time.sleep(hold_s)
        yield ev(0)
    live.stub.streamInputEvent(events(), timeout=5)


def drag(live: Live, x0: int, y0: int, x1: int, y1: int, steps: int, step_s: float) -> None:
    """Press, `steps` moves `step_s` apart, release — one input stream, events sent as they are produced."""
    def events():
        yield ec.InputEvent(mouse_event=ec.MouseEvent(x=x0, y=y0, buttons=1))
        for i in range(1, steps + 1):
            time.sleep(step_s)
            yield ec.InputEvent(mouse_event=ec.MouseEvent(x=round(x0 + (x1 - x0) * i / steps),
                                                          y=round(y0 + (y1 - y0) * i / steps), buttons=1))
        yield ec.InputEvent(mouse_event=ec.MouseEvent(x=x1, y=y1, buttons=0))
    live.stub.streamInputEvent(events(), timeout=30)


def key(live: Live, name: str) -> None:
    live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
        eventType=ec.KeyboardEvent.keypress, key=name))]), timeout=5)


def paste(live: Live, text: str) -> None:
    live.stub.setClipboard(ec.ClipData(text=text), timeout=5)
    time.sleep(0.1)
    key(live, "Paste")


def rotate(live: Live, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
    live.stub.setPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION,
                                                     value=ec.ParameterValue(data=[x, y, z]),
                                                     interpolation=ec.PhysicalModelValue.STEP), timeout=5)


def accel(live: Live) -> list[float]:
    return [round(v, 2) for v in live.stub.getSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION),
                                                     timeout=5).value.data]


def shake_pulse(live: Live, delta: float, hold_s: float = 0.06, settle_s: float = 0.25) -> dict[str, object]:
    """ARCHITECTURE §9.7: hold the baseline 250 ms, push the gravity axis away from zero by delta for 60 ms,
    then clear the override with setPhysicalModel(ROTATION, current, STEP)."""
    base = accel(live)
    axis = max(range(3), key=lambda i: abs(base[i]))
    pulse = list(base)
    pulse[axis] += delta if base[axis] >= 0 else -delta

    def set_acc(v: list[float]) -> None:
        live.stub.setSensor(ec.SensorValue(target=ec.SensorValue.ACCELERATION, value=ec.ParameterValue(data=v)),
                            timeout=5)
    set_acc(base)
    time.sleep(settle_s)
    set_acc(pulse)
    time.sleep(hold_s)
    rot = list(live.stub.getPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION),
                                          timeout=5).value.data)
    live.stub.setPhysicalModel(ec.PhysicalModelValue(target=ec.PhysicalModelValue.ROTATION,
                                                     value=ec.ParameterValue(data=rot),
                                                     interpolation=ec.PhysicalModelValue.STEP), timeout=5)
    return {"base": base, "axis": axis, "pulse": [round(v, 2) for v in pulse], "rotation": rot}


def stylus_sheet(live: Live) -> bool:
    """Gboard's stylus onboarding sheet (an IME window, absent from uiautomator dumps): lavender mid-screen."""
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=720, height=1280), timeout=10)
    w = img.format.width

    def px(x: int, y: int) -> tuple[int, int, int]:
        i = (y * w + x) * 3
        return img.image[i], img.image[i + 1], img.image[i + 2]
    return all(225 <= r <= 250 and 225 <= g <= 250 and b >= r + 3 for r, g, b in (px(360, 330), px(600, 980)))


def scroll_to(live: Live, text: str, quiet_pkg: str | None = None, swipes: int = 6, bottom: int = 1186) -> Node:
    """Swipe the scrollable screen until a node with exactly `text` is fully visible above the nav bar:
    first towards the end, then back towards the top."""
    for up in (True, False):
        for _ in range(swipes + 1):
            d = dump(live, quiet_pkg)
            n = d.find(text=text)
            if n and n.bounds[1] >= 160 and n.bounds[3] <= bottom:
                return n
            y0, y1 = ("1100", "560") if up else ("560", "1100")
            live.shell("input", "swipe", "650", y0, "650", y1, "350")
            time.sleep(0.8)
    raise LookupError(f"not found on screen: {text}")


def permission_dialog(d: Dump) -> dict[str, object] | None:
    msg = d.find(rid_suffix=":id/permission_message")
    if msg is None:
        return None
    return {"message": msg.text, "buttons": [n.text for n in d.nodes if ":id/permission_" in n.rid
                                             and n.rid.endswith("_button") and n.text]}


def answer_permission(live: Live, d: Dump, allow: bool) -> str | None:
    suffixes = (("permission_allow_foreground_only_button", "permission_allow_button") if allow
                else ("permission_deny_button", "permission_deny_and_dont_ask_again_button"))
    for suffix in suffixes:
        n = d.find(rid_suffix=":id/" + suffix)
        if n is not None:
            tap(live, *n.center)
            time.sleep(1.0)
            return n.text
    return None


def frame_rotation(live: Live) -> str:
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.RGB888, width=144, height=256), timeout=10)
    return ec.Rotation.SkinRotation.Name(img.format.rotation.rotation)


def screenshot(live: Live, name: str) -> Path:
    img = live.stub.getScreenshot(ec.ImageFormat(format=ec.ImageFormat.PNG), timeout=15)
    path = OUT / f"{name}.png"
    path.write_bytes(img.image)
    return path


# --- app ---------------------------------------------------------------------------------------------------
def install(live: Live, apk: Path, grant: bool) -> dict[str, object]:
    t = time.monotonic()
    r = live.adb("install", "-r", *(["-g"] if grant else []), str(apk), timeout_s=180)
    return {"grant": grant, "seconds": round(time.monotonic() - t, 2), "result": (r.out + r.err).strip()[-80:]}


def launch(live: Live, pkg: str) -> dict[str, object]:
    comp = live.shell("cmd", "package", "resolve-activity", "--brief", "-c", "android.intent.category.LAUNCHER",
                      pkg).out.strip().splitlines()[-1]
    t = time.monotonic()
    r = live.shell("am", "start", "-W", "-n", comp, timeout_s=60)
    total = re.search(r"TotalTime: (\d+)", r.out)
    return {"component": comp, "seconds": round(time.monotonic() - t, 2),
            "TotalTime_ms": int(total.group(1)) if total else None, "status": re.findall(r"Status: \w+", r.out)}


def resumed(live: Live) -> str:
    out = live.adb("shell", "dumpsys activity activities | grep -m1 -E 'topResumedActivity|mResumedActivity'",
                   timeout_s=15).out
    m = re.search(r"\{[^ ]+ [^ ]+ ([^ }]+)", out)
    return m.group(1) if m else out.strip()[:100]


def pid_of(live: Live, pkg: str) -> str:
    return live.shell("pidof", pkg).out.strip()


# --- logcat (gRPC Text mode) ------------------------------------------------------------------------------------
LINE = re.compile(r"^\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(.*?)\s*: (.*)$")


class Logcat:
    """Collects guest logcat lines over gRPC. The buffer is cleared first; the initial backlog is dropped."""

    def __init__(self, live: Live, settle_s: float = 2.0) -> None:
        live.adb("logcat", "-b", "all", "-c", timeout_s=15)
        self.lines: list[tuple[float, str]] = []
        self.stream = live.stub.streamLogcat(ec.LogMessage(), timeout=3600)
        threading.Thread(target=self._pump, daemon=True).start()
        time.sleep(settle_s)
        self.backlog = len(self.lines)
        self.lines = self.lines[self.backlog:]

    def _pump(self) -> None:
        try:
            for msg in self.stream:
                now = time.monotonic()
                self.lines.extend((now, line) for line in msg.contents.splitlines() if line)
        except Exception:
            pass

    def since(self, t: float, pid: str | None = None, pattern: str | None = None) -> list[str]:
        out = []
        for ts, line in list(self.lines):
            if ts < t:
                continue
            m = LINE.match(line)
            if pid and (not m or m.group(1) != pid):
                continue
            if pattern and not re.search(pattern, line):
                continue
            out.append(line)
        return out

    def wait_for(self, pattern: str, t: float, timeout_s: float, pid: str | None = None) -> str | None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            hits = self.since(t, pid, pattern)
            if hits:
                return hits[0]
            time.sleep(0.2)
        return None

    def close(self) -> None:
        self.stream.cancel()


# --- audio (gRPC streamAudio) ------------------------------------------------------------------------------------
class AudioTap:
    """Peak level of the guest's audio output, per packet arrival time."""

    def __init__(self, live: Live) -> None:
        fmt = ec.AudioFormat(samplingRate=44100, channels=ec.AudioFormat.Mono, format=ec.AudioFormat.AUD_FMT_S16,
                             mode=ec.AudioFormat.MODE_REAL_TIME)
        self.stream = live.stub.streamAudio(fmt, timeout=3600)
        self.peaks: list[tuple[float, int]] = []
        self.error: str | None = None
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            for pkt in self.stream:
                samples = array.array("h", pkt.audio[: len(pkt.audio) // 2 * 2])
                self.peaks.append((time.monotonic(), max((abs(s) for s in samples), default=0)))
        except Exception as e:
            self.error = f"{type(e).__name__}: {str(e)[:120]}"

    def peak(self, t0: float, t1: float) -> dict[str, int]:
        sel = [p for ts, p in self.peaks if t0 <= ts <= t1]
        return {"packets": len(sel), "peak": max(sel, default=0), "loud_packets": sum(p > 1000 for p in sel)}

    def close(self) -> None:
        self.stream.cancel()
