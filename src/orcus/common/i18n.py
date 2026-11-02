"""UI language (ARCHITECTURE.md §10.6): Korean or English.

The setting `ui.language` is "auto" (Windows' display language: Korean -> ko, any other -> en), "ko" or "en". It
applies from the next start: __main__ sets it before any window is made. The CLI stays Korean (a tool for teachers
and developers), so only the window, the setup wizard and the shared message table change.

The GUI keeps its Korean sentences inline. t("...") translates one where it is shown; N_("...") marks one kept in a
table for a later t(). messages_en.GUI maps each Korean sentence to English. A unit test reads every t()/N_() literal
in the source and checks that the English table has it with the same {fields}.
"""

from . import messages_en, win32

LANGUAGES = ("ko", "en")
LANG_KOREAN = 0x12   # the primary language in a Windows LANGID
_current = "ko"


def windows_language() -> str:
    try:
        return "ko" if win32.ui_language_id() & 0x3FF == LANG_KOREAN else "en"
    except (AttributeError, OSError):
        return "ko"


def resolve(setting: str) -> str:
    """The language a `ui.language` value means on this PC ("auto" and unknown values follow Windows)."""
    return setting if setting in LANGUAGES else windows_language()


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGUAGES else "ko"


def language() -> str:
    return _current


def t(ko: str, /, **fields: object) -> str:
    """The sentence in the UI language with its {fields} filled in. One missing from the English table shows in
    Korean (the unit test keeps the table complete)."""
    words = messages_en.GUI.get(ko, ko) if _current == "en" else ko
    return words.format(**fields) if fields else words


def N_(ko: str) -> str:   # noqa: N802  (the gettext name)
    """Marks a sentence kept in a table; t() translates it where it is shown."""
    return ko
