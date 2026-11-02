"""English UI (common.i18n, messages_en): every Korean sentence the window shows has an English one with the
same {fields}, no Korean sentence is left outside t()/N_(), and the language follows the setting."""

import ast
import re
import string
from collections.abc import Iterator
from pathlib import Path

import pytest

from conftest import SRC
from orcus.common import i18n, messages_en, messages_ko
from orcus.contracts import ErrorCode, RemedyAction, Settings

HANGUL = re.compile(r"[가-힣]")
# Korean on purpose: the CLI stays Korean, the table is the Korean table, test data typed into the phone
EXEMPT = {"cli.py", "common/messages_ko.py", "common/messages_en.py", "engine/selftest.py", "app/smoke.py"}
KEEP = {"한국어"}   # a language's own name, shown as is in both languages
DEFAULT_PLACES = {name for _, _, name in Settings().sensors.location_presets}   # data; t() shows them in English


def _sources() -> Iterator[tuple[str, Path]]:
    for path in sorted((SRC / "orcus").rglob("*.py")):
        rel = path.relative_to(SRC / "orcus").as_posix()
        if "/generated/" not in f"/{rel}" and rel not in EXEMPT:
            yield rel, path


def _docstrings(tree: ast.AST) -> set[int]:
    """Bare string statements (docstrings and the like) are notes for developers, not UI."""
    return {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}


def marked_sentences() -> dict[str, list[str]]:
    """Every literal given to t() or N_(), with where it is."""
    found: dict[str, list[str]] = {}
    for rel, path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "N_") \
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                found.setdefault(node.args[0].value, []).append(f"{rel}:{node.lineno}")
    return found


def stray_korean() -> list[str]:
    stray: list[str] = []
    for rel, path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):   # the first argument of t()/N_() is the sentence itself
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("t", "N_") \
                    and node.args:
                skip.add(id(node.args[0]))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr) and id(node) not in skip:
                for part in node.values:
                    skip.add(id(part))
                if any(isinstance(v, ast.Constant) and HANGUL.search(str(v.value)) for v in node.values):
                    stray.append(f"{rel}:{node.lineno} f-string")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip \
                    and HANGUL.search(node.value) and node.value not in KEEP \
                    and not (rel == "contracts/session.py" and node.value in DEFAULT_PLACES):
                stray.append(f"{rel}:{node.lineno} {node.value[:40]!r}")
    return stray


def fields(sentence: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(sentence) if name}


@pytest.fixture
def english() -> Iterator[None]:
    i18n.set_language("en")
    yield
    i18n.set_language("ko")


def test_no_korean_sentence_is_left_untranslatable() -> None:
    assert not stray_korean(), "\n".join(stray_korean())


def test_every_marked_sentence_has_english_with_the_same_fields() -> None:
    found = marked_sentences()
    missing = sorted(f"{k[:50]!r} ({found[k][0]})" for k in found if k not in messages_en.GUI)
    assert not missing, "\n".join(missing)
    for ko, en in messages_en.GUI.items():
        assert fields(ko) == fields(en), ko
        assert not HANGUL.search(en.replace("[가]", "").replace("한/영", "")), en
    unused = sorted(k for k in messages_en.GUI if k not in found and k not in DEFAULT_PLACES)
    assert not unused, unused   # an old sentence nobody shows any more


def test_the_shared_tables_have_english_with_the_same_fields() -> None:
    assert set(messages_en.TEXT) == set(messages_ko.TEXT)
    assert set(messages_en.ERRORS) == set(ErrorCode)
    assert set(messages_en.REMEDY_LABELS) == set(RemedyAction)
    for key, ko in messages_ko.TEXT.items():
        assert fields(ko) == fields(messages_en.TEXT[key]), key
    for table in (messages_en.TEXT, messages_en.ERRORS, messages_en.REMEDY_LABELS):
        for key, en in table.items():
            assert en.strip() and not HANGUL.search(en.replace("'동의'", "")), key   # the CLI asks for 동의 typed
    assert fields(messages_en.IT_REQUEST_WHPX) == fields(messages_ko.IT_REQUEST_WHPX)
    assert fields(messages_en.IT_REQUEST_V3) == fields(messages_ko.IT_REQUEST_V3)


def test_the_built_in_places_have_english_names() -> None:
    assert DEFAULT_PLACES <= set(messages_en.GUI)


def test_text_spec_and_t_follow_the_language(english: None) -> None:
    assert messages_ko.text("engine.state.ready") == "Ready"
    assert messages_ko.text("remedy.retry") == "Try again"
    assert messages_ko.spec(ErrorCode.APP_CRASH).text == messages_en.ERRORS[ErrorCode.APP_CRASH]
    assert messages_ko.spec(ErrorCode.APP_CRASH).channel is messages_ko.ERRORS[ErrorCode.APP_CRASH].channel
    assert i18n.t("로그 ({n})", n=3) == "Log (3)"
    assert i18n.t("이런 문장은 표에 없음") == "이런 문장은 표에 없음"   # shown as is rather than failing
    assert "HypervisorPlatform" in messages_ko.it_request(messages_ko.it_template("whpx"), "x")
    assert "Request" in messages_ko.it_template("whpx")
    i18n.set_language("ko")
    assert messages_ko.text("engine.state.ready") == "준비됨" and i18n.t("로그 ({n})", n=3) == "로그 (3)"


def test_the_setting_picks_the_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n.win32, "ui_language_id", lambda: 0x0412)
    assert i18n.resolve("auto") == "ko" and i18n.resolve("en") == "en"
    monkeypatch.setattr(i18n.win32, "ui_language_id", lambda: 0x0409)
    assert i18n.resolve("auto") == "en" and i18n.resolve("ko") == "ko" and i18n.resolve("fr") == "en"
    i18n.set_language("xx")
    assert i18n.language() == "ko"
    assert Settings().ui.language == "auto"
