"""Run watcher (ARCHITECTURE.md §7.8): sorts the running app's logcat lines into problems. Pure.

The strings are the ones S5 saw with real App Inventor builds on google_apis 36. Priority, highest first:
speech recognizer missing, crash, block runtime error, component error, TTS missing, ANR, died. A FATAL
EXCEPTION is held until its stack ends, so a RECOGNIZE_SPEECH crash (UseLegacy on, first run through the
permission dialog) becomes APP_SPEECH_MISSING instead of APP_CRASH. The three `E Toast … text toasts` lines
that follow an unhandled component error match nothing and are ignored.
"""

import re
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime

from orcus.common import messages_ko
from orcus.contracts import DeviceCaps, ErrorCode, LogEntry, OrcusError, Remedy, RemedyAction

FORM_ERROR = re.compile(r"Form (\S+) ErrorOccurred, errorNumber = (-?\d+), componentType = (.*?), "
                        r"functionName = (.*?), messages = (.*)$")
DIED = re.compile(r"Process (\S+) \(pid (\d+)\) has died")
NO_ACTIVITY = ("ActivityNotFoundException", "No Activity found to handle Intent")
SPEECH_ACTION = "RECOGNIZE_SPEECH"
SPEECH_BIND = "Bind to system recognition service failed"
TTS_FAILED = ("max number of speak retries exceeded", "speak called and tts.speak result was an error")
TTS_NOT_INIT = "speak called when TTS not initialized"
EXPLAINED = frozenset({0, 601, 908, 1501, 2701, 3802, 3806})   # app.error.<n> in messages_ko
CRASH_HOLD_US = 500_000     # a FATAL EXCEPTION stack is written within a few ms
REPEAT_US = 3_000_000       # the same problem again within 3 s is one card (Clock timers repeat errors)
ANR_REPEAT_US = 10_000_000  # WindowManager and ActivityManager may both report one ANR
EXCERPT_LINES = 50


@dataclass(frozen=True)
class Finding:
    error: OrcusError
    ts_us: int
    ended: bool = False   # the app process is gone (crash or died)


def line(e: LogEntry) -> str:
    ts = datetime.fromtimestamp(e.ts_us / 1_000_000).strftime("%m-%d %H:%M:%S.%f")[:-3]
    return f"{ts} {e.pid:5d} {e.tid:5d} {e.level} {e.tag}: {e.msg}"


def _speech_text(msg: str) -> bool:
    return SPEECH_ACTION in msg and any(s in msg for s in NO_ACTIVITY)


class Watcher:
    """One per launch: fed every logcat line on the logcat thread (the owner serialises access)."""

    def __init__(self, package: str, pid: int, caps: DeviceCaps | None) -> None:
        self.package = package
        self.pid = pid
        self._no_tts = caps is not None and caps.tts_engine is None
        self._no_speech = caps is not None and not caps.speech_recognizer
        self._anr = re.compile(rf"ANR in {re.escape(package)}(\s|$)")
        self._recent: deque[LogEntry] = deque(maxlen=EXCERPT_LINES)
        self._crash: list[LogEntry] | None = None
        self._ended = False
        self._seen: dict[tuple[ErrorCode, str], int] = {}
        self._speech_at: int | None = None

    @property
    def ended(self) -> bool:
        return self._ended

    def recent(self) -> list[str]:
        return [line(e) for e in self._recent]

    def feed(self, e: LogEntry) -> list[Finding]:
        mine = e.pid == self.pid
        if mine or self.package in e.msg:
            self._recent.append(e)
        out: list[Finding] = []
        if self._crash is not None:
            fresh = e.ts_us - self._crash[0].ts_us <= CRASH_HOLD_US
            if mine and e.tag == "AndroidRuntime" and fresh:
                self._crash.append(e)
                return out
            if mine or not fresh or self._died(e):
                out += self.flush()
        if self._ended:
            return out
        found = self._classify(e, mine)
        if found is not None:
            out.append(found)
        return out

    def flush(self) -> list[Finding]:
        """Close a held crash stack (end of stream, or the app is being stopped)."""
        block, self._crash = self._crash, None
        if not block:
            return []
        self._ended = True
        text = [b.msg for b in block]
        excerpt = "\n".join(line(b) for b in block[:40])
        speech = next((t for t in text if _speech_text(t)), None)
        if speech is not None:
            err = messages_ko.error(ErrorCode.APP_SPEECH_MISSING, speech, excerpt, message_key="app.speech_missing")
        else:
            first = next((t for t in text[1:] if not t.startswith(("Process:", "\t", " ", "at "))), text[0])
            err = messages_ko.error(ErrorCode.APP_CRASH, first, excerpt)
        return [Finding(err, block[0].ts_us, ended=True)]

    # --- rules ----------------------------------------------------------------------------------------------
    def _classify(self, e: LogEntry, mine: bool) -> Finding | None:
        msg = e.msg
        if not mine:
            if (e.tag == "WindowManager" and "ANR in Window{" in msg and f" {self.package}/" in msg) or \
                    (e.tag == "ActivityManager" and self._anr.search(msg)):
                return self._card(ErrorCode.APP_ANR, e, msg, key="anr", repeat_us=ANR_REPEAT_US)
            if self._died(e):
                self._ended = True
                return Finding(self._error(ErrorCode.APP_DIED, msg), e.ts_us, ended=True)
            return None
        if e.tag == "AndroidRuntime" and msg.startswith("FATAL EXCEPTION"):
            self._crash = [e]
            return None
        if e.tag == "libc" and msg.startswith("Fatal signal"):   # native crash (a WebViewer renderer, Phase 3a)
            self._ended = True
            return Finding(self._error(ErrorCode.APP_CRASH, msg), e.ts_us, ended=True)
        # error 10 (cannot bind) is runtime proof that no recognizer is there, whatever the cached caps say
        if _speech_text(msg) or (e.tag == "SpeechRecognizer" and SPEECH_BIND in msg):
            return self._speech(e)
        if e.tag == "RuntimeErrorAlert" and msg.startswith("alert("):
            if self._speech_at is not None and e.ts_us - self._speech_at <= REPEAT_US:
                return None   # the alert that follows a missing recognizer (UseLegacy on)
            detail = msg.removeprefix("alert(").removesuffix(", End Application")
            return self._card(ErrorCode.APP_AI_RUNTIME_ERROR, e, detail)
        m = FORM_ERROR.search(msg) if e.tag == "Form" else None
        if m is not None:
            number, component, function, text = int(m.group(2)), m.group(3), m.group(4), m.group(5)
            if number == 0 and component == "SpeechRecognizer" and (self._no_speech or self._speech_at is not None):
                return self._speech(e)   # "Try Again." after the bind failure: the same card
            return self._component(e, number, f"{number} · {component}.{function}: {text}")
        if e.tag == "InternalTTS" and (any(s in msg for s in TTS_FAILED) or (self._no_tts and TTS_NOT_INIT in msg)):
            return self._card(ErrorCode.APP_TTS_MISSING, e, msg, key="tts", message_key="app.tts_missing",
                              repeat_us=None)
        return None

    def _died(self, e: LogEntry) -> bool:
        m = DIED.search(e.msg) if e.tag == "ActivityManager" else None
        return m is not None and int(m.group(2)) == self.pid

    def _speech(self, e: LogEntry) -> Finding | None:
        first = self._speech_at is None
        self._speech_at = e.ts_us
        if not first:
            return None   # System.err + RuntimeErrorAlert, or error 10 + errorNumber 0: one card per launch
        err = self._error(ErrorCode.APP_SPEECH_MISSING, e.msg, message_key="app.speech_missing")
        return Finding(err, e.ts_us)

    def _component(self, e: LogEntry, number: int, detail: str) -> Finding | None:
        key = f"app.error.{number}" if number in EXPLAINED else None
        found = self._card(ErrorCode.APP_AI_COMPONENT_ERROR, e, detail, message_key=key)
        if found is not None and number == 908:   # permission denied -> [Allow all permissions]
            return replace(found, error=replace(found.error, remedy=Remedy(RemedyAction.GRANT_ALL,
                                                                           "remedy.grant_all")))
        return found

    def _card(self, code: ErrorCode, e: LogEntry, detail: str, *, key: str | None = None,
              message_key: str | None = None, repeat_us: int | None = REPEAT_US) -> Finding | None:
        """Repeats of (code, key or detail) within repeat_us are dropped; repeat_us None: once per launch."""
        seen = (code, key if key is not None else detail)
        last = self._seen.get(seen)
        if last is not None and (repeat_us is None or e.ts_us - last <= repeat_us):
            return None
        self._seen[seen] = e.ts_us
        return Finding(self._error(code, detail, message_key=message_key), e.ts_us)

    def _error(self, code: ErrorCode, detail: str, *, message_key: str | None = None) -> OrcusError:
        return messages_ko.error(code, detail, "\n".join(self.recent()[-20:]), message_key=message_key)


def crash_from_buffer(text: str, package: str) -> OrcusError | None:
    """The crash buffer when no PID showed up within 5 s of `am start` (§7.6)."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if "FATAL EXCEPTION" in lines[i] and any(f"Process: {package}," in x for x in lines[i + 1:i + 3]):
            block = lines[i:i + 40]
            msgs = [x.split("AndroidRuntime: ", 1)[-1] for x in block]
            excerpt = "\n".join(block)
            speech = next((m for m in msgs if _speech_text(m)), None)
            if speech is not None:
                return messages_ko.error(ErrorCode.APP_SPEECH_MISSING, speech, excerpt,
                                         message_key="app.speech_missing")
            first = next((m for m in msgs[1:] if not m.startswith(("Process:", "\t", " ", "at "))), msgs[0])
            return messages_ko.error(ErrorCode.APP_CRASH, first, excerpt)
    return None
