"""Allow-list import rules between packages (ARCHITECTURE.md §2). Anything not listed is forbidden."""

import ast
import sys
from pathlib import Path

from conftest import SRC

ORCUS = SRC / "orcus"
THIRD_PARTY = {"grpc", "google", "apkInspector", "PySide6", "shiboken6"}
ALLOWED: dict[str, set[str]] = {
    "contracts": set(),
    "common": {"contracts"},
    "emugrpc": {"common", "contracts", "grpc", "google"},
    "setup": {"common", "contracts"},
    "engine": {"common", "contracts", "emugrpc", "grpc", "google"},
    "device": {"common", "contracts", "emugrpc", "grpc", "google"},
    "apk": {"common", "contracts", "apkInspector"},
    "app": {"contracts", "common", "setup", "engine", "device", "apk"},
    "gui": {"contracts", "common", "PySide6", "shiboken6"},
    "__main__": {"app", "gui", "common"},
    "cli": {"app", "contracts", "common"},
    "__init__": set(),
}


def _unit(path: Path) -> str:
    rel = path.relative_to(ORCUS)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _imports(path: Path) -> list[tuple[int, str]]:
    """(line, target) where target is an orcus package name or a third-party top-level module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    rel_pkg = path.relative_to(SRC).with_suffix("").parts[:-1]   # ('orcus', 'setup')
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = rel_pkg[: len(rel_pkg) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
                names = [module] if node.module else [f"{module}.{a.name}" for a in node.names]
            else:
                names = [node.module or ""]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "orcus":
                if len(parts) > 1:
                    found.append((node.lineno, parts[1]))
            elif parts[0] not in sys.stdlib_module_names:
                found.append((node.lineno, parts[0]))
    return found


def test_every_module_respects_the_allow_list() -> None:
    violations: list[str] = []
    files = sorted(p for p in ORCUS.rglob("*.py") if "generated" not in p.parts)
    assert files, "no sources found"
    for path in files:
        unit = _unit(path)
        assert unit in ALLOWED, f"{path}: package {unit!r} missing from the allow-list table"
        for line, target in _imports(path):
            if target == unit:
                continue
            if target not in ALLOWED[unit]:
                violations.append(f"{path.relative_to(SRC)}:{line} {unit} -> {target}")
    assert not violations, "\n".join(violations)


def test_core_packages_never_import_qt() -> None:
    for unit in ("contracts", "common", "setup", "engine", "device", "apk", "emugrpc", "app"):
        assert "PySide6" not in ALLOWED[unit]


def test_third_party_list_is_complete() -> None:
    for allowed in ALLOWED.values():
        for name in allowed:
            assert name in THIRD_PARTY or name in ALLOWED, name
