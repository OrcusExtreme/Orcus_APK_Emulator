"""Fixture spike S8 with OrcusProbe (throwaway): Korean into App Inventor's text fields and a WebViewer page.

Each field: tap (gRPC MouseEvent), setClipboard + Paste key, read back with uiautomator. Also: what NumbersOnly does
with Korean, newlines in MultiLine, how a PasswordTextBox shows up in the dump, ASCII key events into a TextBox
(Gboard ko-KR turned them into jamo on a stock EditText), the clipboard access toast.

Usage: D:\\OrcusDev\\venv\\Scripts\\python.exe tools/spikes/fx_s8.py
"""

import json
import sys
import time

from fx_common import (FIXTURES, Live, Logcat, dump, install, key, launch, package_of, paste, say, screenshot,
                       scroll_to, stylus_sheet, tap)

from harness import LaunchMode
from orcus.emugrpc.generated import emulator_controller_pb2 as ec

APK = FIXTURES / "OrcusProbe.apk"
PROBE = package_of(APK)
KOREAN = "안녕하세요 Orcus"
report: dict[str, object] = {}


def field_text(bounds: tuple[int, int, int, int]) -> dict[str, object]:
    d = dump(live, PROBE)
    n = next((n for n in d.nodes if n.cls == "android.widget.EditText" and abs(n.bounds[1] - bounds[1]) < 40
              and abs(n.bounds[0] - bounds[0]) < 40), None)
    return {"text": n.text, "password": n.password} if n else {"text": None}


def try_field(hint: str, text: str, shot: str | None = None) -> dict[str, object]:
    n = scroll_to(live, hint, PROBE)
    tap(live, *n.center)
    time.sleep(1.2)                       # keyboard comes up, window may pan
    sheet = stylus_sheet(live)
    n2 = dump(live, PROBE).find(text=hint) or n
    t = time.monotonic()
    paste(live, text)
    time.sleep(0.4)
    if shot:
        screenshot(live, shot)            # clipboard toast, if any, is on screen now
    time.sleep(0.8)
    r = {"sent": text, **field_text(n2.bounds), "stylus_sheet": sheet,
         "clipboard_log": [line[19:200] for line in log.since(t, pattern=r"(?i)clipboard")][:4]}
    key(live, "GoBack")                   # hide the keyboard
    time.sleep(0.8)
    return r


def ascii_keys(hint: str, text: str) -> dict[str, object]:
    n = scroll_to(live, hint, PROBE)
    tap(live, *n.center)
    time.sleep(1.2)
    n2 = dump(live, PROBE).find(text=hint) or n
    live.stub.streamInputEvent(iter([ec.InputEvent(key_event=ec.KeyboardEvent(
        eventType=ec.KeyboardEvent.keypress, text=text))]), timeout=5)
    time.sleep(1.0)
    r = {"sent_as_key_text": text, **field_text(n2.bounds)}
    key(live, "GoBack")
    time.sleep(0.8)
    return r


def webviewer() -> dict[str, object]:
    out: dict[str, object] = {}
    d = dump(live, PROBE)
    view = next((n for n in d.nodes if n.cls == "android.webkit.WebView"), None)
    assert view is not None, "no WebView on screen"
    vx1, vy1, vx2, vy2 = view.bounds

    def inside(n) -> bool:   # type: ignore[no-untyped-def]
        x, y = n.center
        return vx1 <= x <= vx2 and vy1 <= y <= vy2
    web = [n for n in d.nodes if n.cls == "android.widget.EditText" and inside(n)]
    out["webview"] = view.bounds
    out["web_nodes_before"] = [(n.cls.split(".")[-1], n.text, n.desc, n.bounds) for n in d.nodes if inside(n)][:8]
    for i, n in enumerate(web[:2]):
        tap(live, *n.center)
        time.sleep(1.2)
        paste(live, KOREAN + f" {i}")
        time.sleep(1.0)
        d2 = dump(live, PROBE)
        out[f"web_field_{i}"] = [x.text for x in d2.nodes if x.cls == "android.widget.EditText"
                                 and abs(x.bounds[1] - n.bounds[1]) < 60][:2]
        key(live, "GoBack")
        time.sleep(0.8)
    return out


with Live(mode=LaunchMode.GOLDEN) as live:
    report["boot"] = dict(live.timings)
    report["install"] = install(live, APK, grant=True)
    report["launch"] = launch(live, PROBE)
    time.sleep(2)
    log = Logcat(live)
    for name, args in (("plain_korean", ("plain", KOREAN, "s8_paste_plain")),
                       ("multi_two_lines", ("multi", "첫째 줄\n둘째 줄")),
                       ("password_korean", ("password", "비밀번호123")),
                       ("number_korean", ("number", KOREAN)),
                       ("number_digits", ("number", "12345"))):
        say(name)
        try:
            report[name] = try_field(*args)
        except Exception as e:
            report[name] = f"FAILED {type(e).__name__}: {e}"
    for name, fn, args in (("ascii_keys_plain", ascii_keys, ("plain", "abc")),):
        say(name)
        try:
            report[name] = fn(*args)
        except Exception as e:
            report[name] = f"FAILED {type(e).__name__}: {e}"
    say("webviewer")
    try:
        scroll_to(live, "voices", PROBE)          # brings the WebViewer (just above that row) on screen
        report["webviewer"] = webviewer()
    except Exception as e:
        report["webviewer"] = f"FAILED {type(e).__name__}: {e}"
    screenshot(live, "s8_end")
    log.close()

sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
