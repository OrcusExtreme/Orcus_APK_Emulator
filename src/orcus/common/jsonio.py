"""JSON state files: tolerant reads, atomic writes."""

import json
import os
import time
from pathlib import Path
from typing import Any

from orcus.common import log

_log = log.get("jsonio")


def read_json(path: Path) -> Any | None:
    """Parsed content, or None when the file does not exist or is not readable JSON. Accepts a UTF-8 BOM.

    A broken file (hand-edited, saved as ANSI/CP949, zeros after a power cut) must not stop the app or setup:
    it is set aside as `<name>.corrupt` for a look and the caller starts from its defaults."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (ValueError, RecursionError) as e:   # JSONDecodeError and UnicodeDecodeError are ValueErrors
        _log.warning("%s is not readable JSON, set aside as .corrupt: %s", path, e)
        try:
            os.replace(path, path.with_name(path.name + ".corrupt"))
        except OSError:
            pass
        return None


def write_json_atomic(path: Path, data: Any) -> None:
    """Write next to the target, flush it to disk, and os.replace it, retrying briefly when a scanner holds the
    file (a replace before the data is on disk can leave zeros after a power cut)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))
