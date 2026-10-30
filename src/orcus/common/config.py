"""Settings layers (ARCHITECTURE.md §11.3): command line > ORCUS_* > state\\settings.json > defaults.

Keys are dotted ('engine.gpu_mode'). In the environment a double underscore stands for the dot:
ORCUS_ENGINE__GPU_MODE=software. The built-in defaults are the dataclass defaults in
orcus.contracts.session (a separate defaults.toml would only duplicate them).
"""

import dataclasses
import json
import os
import types
import typing
from collections.abc import Mapping
from enum import Enum
from typing import Any

from orcus.contracts import ApplyResult, Settings

from . import jsonio
from .paths import OrcusPaths

ENV_PREFIX = "ORCUS_"
ENV_IGNORED = frozenset({"ORCUS_HOME", "ORCUS_DEV_ROOT"})
REBUILD_KEYS = frozenset({"engine.gpu_mode", "engine.ram_mb", "engine.cores", "engine.locale", "engine.timezone"})
RESTART_KEYS_PREFIX = ("engine.", "session.keep_state")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _coerce(value: Any, tp: Any, key: str) -> Any:
    origin = typing.get_origin(tp)
    if isinstance(value, str) and tp is not str and origin is None and isinstance(tp, type) \
            and dataclasses.is_dataclass(tp):
        value = json.loads(value)
    if isinstance(value, str) and origin in (tuple, Mapping, dict, typing.Union, types.UnionType):
        value = json.loads(value)
    if tp is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            return value.strip().lower() in _TRUE
        raise ValueError(f"{key}: expected true/false, got {value!r}")
    if tp is int:
        if isinstance(value, bool):
            raise ValueError(f"{key}: expected a number, got {value!r}")
        return int(value)
    if tp is float:
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            raise ValueError(f"{key}: expected text, got {value!r}")
        return value
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        if not isinstance(value, Mapping):
            raise ValueError(f"{key}: expected an object")
        return _build(tp, value, key)
    if origin in (Mapping, dict):
        _, value_tp = typing.get_args(tp)
        if not isinstance(value, Mapping):
            raise ValueError(f"{key}: expected an object")
        return types.MappingProxyType({str(k): _coerce(v, value_tp, f"{key}.{k}") for k, v in value.items()})
    if origin is tuple:
        args = typing.get_args(tp)
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{key}: expected a list")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_coerce(v, args[0], key) for v in value)
        if len(args) != len(value):
            raise ValueError(f"{key}: expected {len(args)} items")
        return tuple(_coerce(v, a, key) for v, a in zip(value, args))
    raise ValueError(f"{key}: unsupported type {tp!r}")


def _build(cls: type, data: Mapping[str, Any], prefix: str) -> Any:
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{prefix}: unknown keys {sorted(unknown)}")
    defaults = cls()
    kwargs = {name: _coerce(data[name], hints[name], f"{prefix}.{name}".lstrip("."))
              if name in data else getattr(defaults, name) for name in known}
    return cls(**kwargs)


def to_plain(value: Any) -> Any:
    """Settings (or a section) as JSON-compatible data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [to_plain(v) for v in value]
    return value


def flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = f"{prefix}{k}"
        if isinstance(v, Mapping) and k != "shake_sensitivity":
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _nest(changes: Mapping[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    root: dict[str, Any] = json.loads(json.dumps(base)) if base else {}
    for dotted, value in changes.items():
        node = root
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ValueError(f"{dotted}: not a section")
        node[leaf] = to_plain(value)
    return root


def apply(settings: Settings, changes: Mapping[str, Any]) -> Settings:
    """New Settings with dotted changes applied. Raises ValueError for unknown keys or bad values."""
    merged = _nest(changes, to_plain(settings))
    return _build(Settings, merged, "")


def classify(old: Settings, new: Settings) -> ApplyResult:
    a, b = flatten(to_plain(old)), flatten(to_plain(new))
    changed = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
    rebuild = bool(changed & REBUILD_KEYS)
    restart = rebuild or any(k.startswith(RESTART_KEYS_PREFIX) for k in changed)
    return ApplyResult(needs_restart=restart, needs_rebuild=rebuild)


def env_overrides(env: Mapping[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in env.items():
        upper = name.upper()
        if upper.startswith(ENV_PREFIX) and upper not in ENV_IGNORED:
            out[upper.removeprefix(ENV_PREFIX).lower().replace("__", ".")] = value
    return out


def load(paths: OrcusPaths, *, env: Mapping[str, str] | None = None,
         cli: Mapping[str, Any] | None = None, warnings: list[str] | None = None) -> Settings:
    """Bad entries in settings.json or ORCUS_* are skipped (reported in `warnings`), never fatal."""
    settings = Settings()
    stored = jsonio.read_json(paths.state_file("settings.json"))
    layers: list[Mapping[str, Any]] = [
        flatten(stored) if isinstance(stored, Mapping) else {},
        env_overrides(os.environ if env is None else env),
        dict(cli or {}),
    ]
    for layer in layers:
        for key, value in layer.items():
            try:
                settings = apply(settings, {key: value})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                if warnings is not None:
                    warnings.append(str(e))
    return settings


def save_overrides(paths: OrcusPaths, changes: Mapping[str, Any]) -> None:
    """Merge dotted changes into settings.json (validated first)."""
    apply(Settings(), changes)
    stored = jsonio.read_json(paths.state_file("settings.json"))
    jsonio.write_json_atomic(paths.state_file("settings.json"),
                             _nest(changes, stored if isinstance(stored, dict) else None))
