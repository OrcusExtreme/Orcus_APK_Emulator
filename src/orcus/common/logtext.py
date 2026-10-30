"""Logcat threadtime text -> LogEntry. `adb logcat -v threadtime` and gRPC Text mode print the same lines."""

import re
from datetime import datetime

from orcus.contracts import LogEntry

THREADTIME = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEFA])\s+(.*?)\s*: (.*)$")
ENTRY_START = re.compile(r"(?=\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}\s+\d+\s+\d+\s+[VDIWEFA]\s)")


def parse(line: str, *, year: int | None = None) -> LogEntry | None:
    """None for anything that is not an entry ("--------- beginning of main", a cut line)."""
    m = THREADTIME.match(line)
    if m is None:
        return None
    mon, day, hh, mm, ss, ms = (int(m.group(i)) for i in range(1, 7))
    try:
        ts = datetime(year or datetime.now().year, mon, day, hh, mm, ss, ms * 1000).timestamp()
    except ValueError:
        return None
    return LogEntry(ts_us=int(ts * 1_000_000), pid=int(m.group(7)), tid=int(m.group(8)), level=m.group(9),
                    tag=m.group(10), msg=m.group(11))


def split_entries(contents: str) -> list[str]:
    """Entries glued together without a newline (gRPC Text mode) are split where the next timestamp starts."""
    return [p.strip("\r\n") for p in ENTRY_START.split(contents) if p.strip()]
