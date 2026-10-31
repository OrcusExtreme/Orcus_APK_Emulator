"""Early-exit classification from the emulator log tail (ARCHITECTURE.md §5.1 table)."""

import re
from dataclasses import dataclass

from orcus.contracts import ErrorCode


@dataclass(frozen=True)
class ExitClass:
    code: ErrorCode | None          # None: the golden did not load (rebuild instead of failing)
    golden_load_failed: bool = False
    retry: bool = False             # AVD lock / ports: one more try after cleanup or other ports
    gpu_changed: bool = False       # the emulator found another host GPU and deleted the golden (3a finding 18)


_RULES: tuple[tuple[str, ExitClass], ...] = (
    (r"PANIC: Broken AVD system path|Cannot find AVD system path", ExitClass(ErrorCode.PRE_SDK_MISSING)),
    (r"Not enough space", ExitClass(ErrorCode.PRE_DISK)),
    (r"Please use -read-only", ExitClass(ErrorCode.ENGINE_AVD_LOCKED, retry=True)),
    (r"(?i)address already in use|port \d+ is (already )?(used|in use)|failed to bind", ExitClass(ErrorCode.PRE_PORTS,
                                                                                                   retry=True)),
    (r"(?i)hardware acceleration|WHPX is not|whpx.*(not available|error|fail)|HAXM is not installed|"
     r"x86(_64)? emulation currently requires", ExitClass(ErrorCode.PRE_WHPX_OFF)),
)
_GOLDEN = re.compile(r"(?i)failed to load snapshot|delete existing snapshot|snapshot.*(invalid|incompatible)")


def classify(log_tail: str, *, golden: bool) -> ExitClass:
    for pattern, result in _RULES:
        if re.search(pattern, log_tail):
            return result
    if golden and _GOLDEN.search(log_tail):
        return ExitClass(None, golden_load_failed=True, gpu_changed="host GPU has changed" in log_tail)
    return ExitClass(ErrorCode.ENGINE_EXIT)


def tail(text: str, lines: int = 20) -> str:
    return "\n".join(text.splitlines()[-lines:])
