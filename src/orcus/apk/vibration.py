"""Vibrations an app made: no log line says so (S5), but `dumpsys vibrator_manager` (0.05 s) lists the one running,
with its creation time and the calling package, and the recent ones by usage, one line each with their length.
Seen on API 36 (HelloPurr's Sound.Vibrate(500) on a tap):

        Vibration:
          status = running
          createTime = 10-31 18:15:58.988
          callerInfo = CallerInfo{ uid=10216, opPkg=appinventor.ai_x.HelloPurr, deviceId=0, attrs=...}
    and once it ended, under "TOUCH:":
          10-31 18:15:58.988 |   effect |   finished | duration:   536ms | start: ... | usage: TOUCH | <pkg> (uid=10216,

The creation time and the package name one vibration in both places.
"""

import re
from dataclasses import dataclass

CMD = ("dumpsys", "vibrator_manager")
_DONE = re.compile(r"^\s*(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) \|[^|\n]*\|[^|\n]*\| duration:\s*(\d+)ms\b[^\n]*?"
                   r"\| usage: \w+ \| (\S+) \(uid=", re.M)
_CALLER = re.compile(r"opPkg=([^,\s}]+)")


@dataclass(frozen=True)
class Buzz:
    key: str           # creation time and package: the same vibration while it runs and once it ended
    package: str
    duration_ms: int   # -1 while it runs


def parse(text: str) -> list[Buzz]:
    found: dict[str, Buzz] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "Vibration:":
            continue
        depth = len(line) - len(line.lstrip())
        fields: dict[str, str] = {}
        for nxt in lines[i + 1:]:
            if len(nxt) - len(nxt.lstrip()) <= depth:
                break
            name, _, value = nxt.strip().partition(" = ")
            fields[name] = value
        caller = _CALLER.search(fields.get("callerInfo", ""))
        if fields.get("status") == "running" and fields.get("createTime") and caller:
            key = f"{fields['createTime']} {caller.group(1)}"
            found[key] = Buzz(key, caller.group(1), -1)
    for m in _DONE.finditer(text):
        key = f"{m.group(1)} {m.group(3)}"
        found[key] = Buzz(key, m.group(3), int(m.group(2)))   # ended: its length is known now
    return list(found.values())
