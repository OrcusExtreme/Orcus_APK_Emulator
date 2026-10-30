"""JSON state files: tolerant reads, atomic writes."""

import json
import os
import time
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any | None:
    """Parsed content, or None when the file does not exist. Accepts a UTF-8 BOM."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    return json.loads(text)


def write_json_atomic(path: Path, data: Any) -> None:
    """Write next to the target and os.replace it, retrying briefly when a scanner holds the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))
