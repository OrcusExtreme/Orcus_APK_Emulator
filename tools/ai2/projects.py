"""The App Inventor fixtures of docs/fixtures.md, as data: HelloPurr, TalkToMe, OrcusProbe.

HelloPurr and TalkToMe follow the official tutorials' designs and blocks; their media (kitty.png, meow.wav) are
drawn and synthesized here, so nothing third-party is bundled. Label texts in OrcusProbe start with a fixed word
("touch ", "drag ", ...) because the device tests find them by text in `uiautomator dump`; no button text starts
with one of those words.
"""

import io
import math
import struct
import wave
import zlib
from collections.abc import Callable

from ai2 import aia
from ai2 import blocks as b
from ai2.aia import FILL_PARENT, Component, Project, Screen

WHITE, BLUE, YELLOW = "&HFFFFFFFF", "&HFF0000FF", "&HFFFFFF00"
SCREEN1 = {"AppName": "", "Title": "Screen1", "Theme": "AppTheme.Light.DarkActionBar", "ActionBar": "True",
           "Sizing": "Responsive"}


# --- small builders -----------------------------------------------------------------------------------------
def label(name: str, text: str, **props: str) -> Component:
    return Component("Label", name, {"Text": text, **props})


def button(name: str, text: str, **props: str) -> Component:
    return Component("Button", name, {"Text": text, **props})


def row(name: str, *children: Component) -> Component:
    return Component("HorizontalArrangement", name, {"Width": FILL_PARENT}, list(children))


def component(ctype: str, name: str, **props: str) -> Component:
    return Component(ctype, name, dict(props))


def set_text(lbl: str, value: b.Expr) -> b.Set:
    return b.Set(lbl, "Label", "Text", value)


def join(*parts: b.Expr | str) -> b.Join:
    return b.Join([b.Text(p) if isinstance(p, str) else p for p in parts])


def dec(param: str, places: int) -> b.Decimal:
    return b.Decimal(b.Param(param), places)


def click(btn: str, *body: b.Stmt) -> b.Event:
    return b.Event(btn, "Button", "Click", [], list(body))


def call(comp: str, ctype: str, method: str, *args: b.Expr, types: tuple[str, ...] = ()) -> b.Call:
    assert len(args) == len(types), f"{comp}.{method}: one YAIL type per argument"
    return b.Call(comp, ctype, method, list(args), list(types))


def incr(var: str) -> b.SetGlobal:
    return b.SetGlobal(var, b.Add(b.Global(var), b.Num(1)))


def screen1(app_name: str, **props: str) -> dict[str, str]:
    return SCREEN1 | {"AppName": app_name} | props


# --- HelloPurr ----------------------------------------------------------------------------------------------
def hello_purr() -> Project:
    s = Screen("Screen1", screen1("HelloPurr"), [
        label("Label1", "Pet the Kitty", BackgroundColor=BLUE, FontSize="30", TextColor=YELLOW),
        button("Button1", "", Image="kitty.png"),
        component("Sound", "Sound1", Source="meow.wav"),
        component("AccelerometerSensor", "AccelerometerSensor1"),
    ], events=[
        click("Button1", call("Sound1", "Sound", "Play"),
              call("Sound1", "Sound", "Vibrate", b.Num(500), types=("number",))),
        b.Event("AccelerometerSensor1", "AccelerometerSensor", "Shaking", [], [call("Sound1", "Sound", "Play")]),
    ])
    return Project("HelloPurr", [s], {"kitty.png": kitty_png(), "meow.wav": meow_wav()})


# --- TalkToMe -----------------------------------------------------------------------------------------------
def talk_to_me() -> Project:
    speak = lambda msg: call("TextToSpeech1", "TextToSpeech", "Speak", msg, types=("text",))  # noqa: E731
    s = Screen("Screen1", screen1("TalkToMe"), [
        component("TextBox", "TextBox1", Hint="Type something", Width=FILL_PARENT),
        button("Button1", "Talk To Me"),
        component("TextToSpeech", "TextToSpeech1"),
        component("AccelerometerSensor", "AccelerometerSensor1"),
    ], events=[
        click("Button1", speak(b.Get("TextBox1", "TextBox", "Text"))),
        b.Event("AccelerometerSensor1", "AccelerometerSensor", "Shaking", [], [speak(b.Text("Stop shaking me!"))]),
    ])
    return Project("TalkToMe", [s])


# --- OrcusProbe ---------------------------------------------------------------------------------------------
def orcus_probe() -> Project:
    acc, tts, rec = "AccelerometerSensor", "TextToSpeech", "SoundRecorder"
    s1 = Screen("Screen1", screen1("OrcusProbe", Title="OrcusProbe", Scrollable="True"), [
        component("Canvas", "Canvas1", Width=FILL_PARENT, Height="300", BackgroundColor=WHITE),
        row("rowTouch", label("lblTouch", "touch -"), label("lblDrag", "drag 0"), label("lblFling", "fling -"),
            button("btnResetTouch", "reset")),
        row("rowSens", button("btnWeak", "weak"), button("btnModerate", "moderate"), button("btnStrong", "strong"),
            label("lblSens", "sens 2")),
        row("rowShake", label("lblShake", "shake 0"), label("lblBack", "back 0"),
            label("lblScreenOrient", "screen -")),
        label("lblAccel", "accel -"),
        label("lblOrient", "orient -"),
        label("lblLoc", "loc -"),
        row("rowText1", component("TextBox", "txtPlain", Hint="plain", Width=FILL_PARENT),
            component("TextBox", "txtMulti", Hint="multi", MultiLine="True", Width=FILL_PARENT)),
        row("rowText2", component("PasswordTextBox", "txtPassword", Hint="password", Width=FILL_PARENT),
            component("TextBox", "txtNumber", Hint="number", NumbersOnly="True", Width=FILL_PARENT)),
        component("WebViewer", "WebViewer1", Width=FILL_PARENT, Height="120",
                  HomeUrl="file:///android_asset/probe.html"),
        row("rowTts", button("btnSpeak", "speak"), button("btnLangs", "voices"), label("lblTts", "tts -")),
        label("lblLangs", "langs -"),
        row("rowRec", button("btnRecStart", "record"), button("btnRecStop", "stop"), label("lblRec", "rec -")),
        row("rowSpeech", button("btnSpeechLegacy", "listen (legacy)"), button("btnSpeechNew", "listen (new)")),
        label("lblSpeech", "speech -"),
        row("rowErr", button("btnScan", "scan"), button("btnBlockError", "block error"),
            button("btnBadIntent", "bad intent")),
        row("rowNav", button("btnAnr", "ANR loop"), button("btnScreen2", "open Screen2")),
        component(acc, "AccelerometerSensor1"),
        component("OrientationSensor", "OrientationSensor1"),
        component("LocationSensor", "LocationSensor1", TimeInterval="1000", DistanceInterval="0"),
        component(tts, "TextToSpeech1", Language="ko"),
        component(rec, "SoundRecorder1"),
        component("Player", "Player1"),
        component("SpeechRecognizer", "SpeechRecognizer1"),                    # UseLegacy default: True
        component("SpeechRecognizer", "SpeechRecognizer2", UseLegacy="False"),
        component("BarcodeScanner", "BarcodeScanner1"),                        # UseExternalScanner default: True
        component("ActivityStarter", "ActivityStarter1", Action="orcus.invalid.ACTION"),
    ], globals=[b.GlobalDecl(g, b.Num(0)) for g in ("dragCount", "shakeCount", "backCount", "anrCount")])

    def sens(btn: str, level: int) -> b.Event:
        return click(btn, b.Set("AccelerometerSensor1", acc, "Sensitivity", b.Num(level), "number"),
                     set_text("lblSens", b.Text(f"sens {level}")))

    def heard(recognizer: str) -> b.Event:
        return b.Event(recognizer, "SpeechRecognizer", "AfterGettingText", ["result", "partial"],
                       [set_text("lblSpeech", join("speech ", b.Param("result")))])

    s1.events = [
        b.Event("Screen1", "Form", "BackPressed", [], [incr("backCount"), set_text("lblBack", join("back ", b.Global("backCount")))]),
        b.Event("Screen1", "Form", "ScreenOrientationChanged", [],
                [set_text("lblScreenOrient", join("screen ", b.Get("Screen1", "Form", "ScreenOrientation")))]),
        b.Event("Canvas1", "Canvas", "Touched", ["x", "y", "touchedAnySprite"],
                [set_text("lblTouch", join("touch ", dec("x", 0), ",", dec("y", 0)))]),
        b.Event("Canvas1", "Canvas", "Dragged",
                ["startX", "startY", "prevX", "prevY", "currentX", "currentY", "draggedAnySprite"],
                [incr("dragCount"), set_text("lblDrag", join("drag ", b.Global("dragCount")))]),
        b.Event("Canvas1", "Canvas", "Flung", ["x", "y", "speed", "heading", "xvel", "yvel", "flungSprite"],
                [set_text("lblFling", join("fling ", dec("speed", 2)))]),
        click("btnResetTouch", b.SetGlobal("dragCount", b.Num(0)), set_text("lblTouch", b.Text("touch -")),
              set_text("lblDrag", b.Text("drag 0")), set_text("lblFling", b.Text("fling -"))),
        b.Event("AccelerometerSensor1", acc, "Shaking", [],
                [incr("shakeCount"), set_text("lblShake", join("shake ", b.Global("shakeCount")))]),
        sens("btnWeak", 1), sens("btnModerate", 2), sens("btnStrong", 3),
        b.Event("AccelerometerSensor1", acc, "AccelerationChanged", ["xAccel", "yAccel", "zAccel"],
                [set_text("lblAccel", join("accel ", dec("xAccel", 1), ",", dec("yAccel", 1), ",", dec("zAccel", 1)))]),
        b.Event("OrientationSensor1", "OrientationSensor", "OrientationChanged", ["azimuth", "pitch", "roll"],
                [set_text("lblOrient", join("orient ", dec("azimuth", 0), ",", dec("pitch", 0), ",", dec("roll", 0)))]),
        b.Event("LocationSensor1", "LocationSensor", "LocationChanged", ["latitude", "longitude", "altitude", "speed"],
                [set_text("lblLoc", join("loc ", dec("latitude", 4), ",", dec("longitude", 4)))]),
        click("btnSpeak", call("TextToSpeech1", tts, "Speak", b.Text("안녕하세요"), types=("text",))),
        b.Event("TextToSpeech1", tts, "AfterSpeaking", ["result"], [set_text("lblTts", join("tts ", b.Param("result")))]),
        click("btnLangs", set_text("lblLangs", join("langs ", b.Get("TextToSpeech1", tts, "AvailableLanguages")))),
        click("btnRecStart", call("SoundRecorder1", rec, "Start")),
        click("btnRecStop", call("SoundRecorder1", rec, "Stop")),
        b.Event("SoundRecorder1", rec, "AfterSoundRecorded", ["sound"],
                [b.Set("Player1", "Player", "Source", b.Param("sound")), call("Player1", "Player", "Start"),
                 set_text("lblRec", b.Text("rec ok"))]),
        click("btnSpeechLegacy", call("SpeechRecognizer1", "SpeechRecognizer", "GetText")),
        click("btnSpeechNew", call("SpeechRecognizer2", "SpeechRecognizer", "GetText")),
        heard("SpeechRecognizer1"), heard("SpeechRecognizer2"),
        click("btnScan", call("BarcodeScanner1", "BarcodeScanner", "DoScan")),
        click("btnBlockError", b.EvalIgnore(b.SelectItem(b.EmptyList(), b.Num(5)))),
        click("btnBadIntent", call("ActivityStarter1", "ActivityStarter", "StartActivity")),
        click("btnAnr", b.ForRange("number", b.Num(1), b.Num(30000000), b.Num(1), [incr("anrCount")])),
        click("btnScreen2", b.OpenScreen("Screen2")),
    ]
    s2 = Screen("Screen2", {"Title": "Screen2", "ScreenOrientation": "landscape"}, [
        label("lblScreen2", "Screen2 landscape"),
        component("Canvas", "Canvas2", Width=FILL_PARENT, Height="200", BackgroundColor=WHITE),
        label("lblTouch2", "touch2 -"),
        button("btnBack2", "close"),
    ], events=[
        b.Event("Canvas2", "Canvas", "Touched", ["x", "y", "touchedAnySprite"],
                [set_text("lblTouch2", join("touch2 ", dec("x", 0), ",", dec("y", 0)))]),
        click("btnBack2", b.CloseScreen()),
    ])
    return Project("OrcusProbe", [s1, s2], {"probe.html": PROBE_HTML})


PROBE_HTML = (b'<!doctype html><html><head><meta charset="utf-8">'
              b'<meta name="viewport" content="width=device-width"></head>'
              b'<body><input id="w" placeholder="web input"><textarea id="t"></textarea></body></html>\n')

# --- 3a acceptance (ARCHITECTURE.md §12.4): the tutorial apps students start with, and three HelloPurr variants ---
RED, BLUE_PAINT, GREEN = "&HFFFF0000", "&HFF0000FF", "&HFF00FF00"


def paint_pot() -> Project:
    """PaintPot tutorial: touch draws a dot, drag draws a line (B2: lines must not break)."""
    color = lambda btn, value: click(btn, b.Set("Canvas1", "Canvas", "PaintColor", b.Num(value), "number"))  # noqa: E731
    s = Screen("Screen1", screen1("PaintPot", Title="PaintPot"), [
        row("rowColors", button("ButtonRed", "Red"), button("ButtonBlue", "Blue"), button("ButtonGreen", "Green")),
        component("Canvas", "Canvas1", Width=FILL_PARENT, Height=FILL_PARENT, BackgroundColor=WHITE,
                  PaintColor=RED, LineWidth="5"),
        button("ButtonWipe", "Wipe"),
    ], events=[
        b.Event("Canvas1", "Canvas", "Touched", ["x", "y", "touchedAnySprite"],
                [call("Canvas1", "Canvas", "DrawCircle", b.Param("x"), b.Param("y"), b.Num(5), b.Bool(True),
                      types=("number", "number", "number", "boolean"))]),
        b.Event("Canvas1", "Canvas", "Dragged",
                ["startX", "startY", "prevX", "prevY", "currentX", "currentY", "draggedAnySprite"],
                [call("Canvas1", "Canvas", "DrawLine", b.Param("prevX"), b.Param("prevY"), b.Param("currentX"),
                      b.Param("currentY"), types=("number", "number", "number", "number"))]),
        color("ButtonRed", -65536), color("ButtonBlue", -16776961), color("ButtonGreen", -16711936),
        click("ButtonWipe", call("Canvas1", "Canvas", "Clear")),
    ])
    return Project("PaintPot", [s])


def ball_bounce() -> Project:
    """BallBounce tutorial: fling the ball, it bounces off the edges (B2: the fling speed follows the mouse)."""
    s = Screen("Screen1", screen1("BallBounce", Title="BallBounce"), [
        component("Canvas", "Canvas1", Width=FILL_PARENT, Height=FILL_PARENT, BackgroundColor=WHITE),
    ], events=[
        b.Event("Ball1", "Ball", "Flung", ["x", "y", "speed", "heading", "xvel", "yvel"], [
            b.Set("Ball1", "Ball", "Heading", b.Param("heading"), "number"),
            b.Set("Ball1", "Ball", "Speed", b.Param("speed"), "number"),
        ]),
        b.Event("Ball1", "Ball", "EdgeReached", ["edge"],
                [call("Ball1", "Ball", "Bounce", b.Param("edge"), types=("number",))]),
    ])
    s.components[0].children.append(component("Ball", "Ball1", Radius="15", PaintColor=BLUE_PAINT, X="150", Y="150",
                                               Interval="20"))
    return Project("BallBounce", [s])


def mole_mash() -> Project:
    """MoleMash tutorial (short form): the mole jumps every second; touching it scores."""
    def move() -> b.Call:
        span = lambda dim, sprite: b.Sub(b.Get("Canvas1", "Canvas", dim), b.Get("Mole", "ImageSprite", sprite))  # noqa: E731
        return call("Mole", "ImageSprite", "MoveTo", b.RandomInt(b.Num(0), span("Width", "Width")),
                    b.RandomInt(b.Num(0), span("Height", "Height")), types=("number", "number"))
    s = Screen("Screen1", screen1("MoleMash", Title="MoleMash"), [
        component("Canvas", "Canvas1", Width=FILL_PARENT, Height="320", BackgroundColor=WHITE),
        label("lblScore", "Score: 0", FontSize="20"),
        button("btnReset", "Reset"),
        component("Clock", "Clock1", TimerInterval="1000"),
    ], globals=[b.GlobalDecl("score", b.Num(0))], events=[
        b.Event("Clock1", "Clock", "Timer", [], [move()]),
        b.Event("Mole", "ImageSprite", "Touched", ["x", "y"], [
            incr("score"), set_text("lblScore", join("Score: ", b.Global("score"))), move()]),
        click("btnReset", b.SetGlobal("score", b.Num(0)), set_text("lblScore", b.Text("Score: 0"))),
    ])
    s.components[0].children.append(component("ImageSprite", "Mole", Picture="mole.png", X="40", Y="40"))
    return Project("MoleMash", [s], {"mole.png": mole_png()})


def hello_purr_variant(name: str, **screen: str) -> Callable[[], Project]:
    def make() -> Project:
        p = hello_purr()
        p.name = name
        p.screens[0].props = p.screens[0].props | {"AppName": name} | screen
        return p
    return make


def hello_purr_min36() -> Project:
    p = hello_purr_variant("HelloPurrMin36")()
    p.settings = {"androidminsdk": "36"}   # Project Properties > AndroidMinSdk (stored in project.properties)
    return p


ALL: dict[str, Callable[[], Project]] = {
    "HelloPurr": hello_purr, "TalkToMe": talk_to_me, "OrcusProbe": orcus_probe,
    "PaintPot": paint_pot, "BallBounce": ball_bounce, "MoleMash": mole_mash,
    "HelloPurrKo": hello_purr_variant("HelloPurrKo", AppName="고양이 쓰다듬기"),   # Korean label (B8)
    "HelloPurrMin36": hello_purr_min36,                                          # minSdk 36 installs (B8)
    "HelloPurrLand": hello_purr_variant("HelloPurrLand", ScreenOrientation="landscape"),   # turned first (B8)
}


# --- media --------------------------------------------------------------------------------------------------
def kitty_png(size: int = 240) -> bytes:
    """A flat cartoon cat face (RGBA PNG), drawn pixel by pixel."""
    orange, dark, pink, white = (244, 163, 64, 255), (40, 40, 40, 255), (232, 122, 144, 255), (255, 255, 255, 255)
    s = size / 240

    def tri(p, a, b_, c):
        def side(p1, p2, p3):
            return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
        d1, d2, d3 = side(p, a, b_), side(p, b_, c), side(p, c, a)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    def disc(p, cx, cy, r):
        return (p[0] - cx * s) ** 2 + (p[1] - cy * s) ** 2 <= (r * s) ** 2

    ears = [((40 * s, 110 * s), (62 * s, 18 * s), (112 * s, 64 * s)),
            ((200 * s, 110 * s), (178 * s, 18 * s), (128 * s, 64 * s))]
    rows = []
    for y in range(size):
        row = bytearray([0])                                      # PNG filter: none
        for x in range(size):
            p = (x + 0.5, y + 0.5)
            px = (0, 0, 0, 0)
            if disc(p, 120, 138, 86) or any(tri(p, *e) for e in ears):
                px = orange
            if disc(p, 88, 122, 16) or disc(p, 152, 122, 16):
                px = white
            if disc(p, 90, 124, 9) or disc(p, 150, 124, 9):
                px = dark
            if tri(p, (110 * s, 158 * s), (130 * s, 158 * s), (120 * s, 170 * s)):
                px = pink
            if any(abs(p[1] - (yy + k * (p[0] - xx)) * s) < 1.6 * s and lo * s <= p[0] <= hi * s
                   for xx, yy, k, lo, hi in ((60, 170, 0.12, 30, 96), (60, 182, -0.08, 30, 96),
                                             (180, 170, -0.12, 144, 210), (180, 182, 0.08, 144, 210))):
                px = dark
            row += bytes(px)
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def _png(width: int, height: int, pixel: Callable[[float, float], tuple[int, int, int, int]]) -> bytes:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row += bytes(pixel(x + 0.5, y + 0.5))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def mole_png(size: int = 64) -> bytes:
    """A brown mole face with a pink nose (RGBA PNG)."""
    brown, dark, pink = (121, 85, 61, 255), (30, 30, 30, 255), (232, 122, 144, 255)
    c = size / 2

    def pixel(x: float, y: float) -> tuple[int, int, int, int]:
        def disc(cx: float, cy: float, r: float) -> bool:
            return (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2
        if disc(c - size * 0.16, c - size * 0.08, size * 0.06) or disc(c + size * 0.16, c - size * 0.08, size * 0.06):
            return dark
        if disc(c, c + size * 0.12, size * 0.09):
            return pink
        return brown if disc(c, c, size * 0.46) else (0, 0, 0, 0)
    return _png(size, size, pixel)


def meow_wav(rate: int = 22050, seconds: float = 0.7) -> bytes:
    """A synthesized 'meow': a rising-then-falling glide with a few harmonics, 16-bit mono PCM."""
    n = int(rate * seconds)
    frames = bytearray()
    phase = 0.0
    for i in range(n):
        t = i / n
        f = 520 + 380 * math.sin(math.pi * min(1.0, t * 1.4)) - 120 * t
        phase += 2 * math.pi * f / rate
        env = min(1.0, t / 0.08) * min(1.0, (1 - t) / 0.3)
        v = env * (0.6 * math.sin(phase) + 0.25 * math.sin(2 * phase) + 0.1 * math.sin(3 * phase))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 26000))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def check_all() -> dict[str, list[str]]:
    return {name: aia.problems(make()) for name, make in ALL.items()}
