"""Shortcuts and phone keys in one place (ARCHITECTURE.md §9.4, §9.5).

The unit test checks the shortcuts for duplicates and for clashes with the keys a phone text box needs
(Ctrl+←/→ and Ctrl+Shift+←/→ are left to word moves and selections).
"""

from PySide6.QtCore import Qt

SHORTCUTS: dict[str, str] = {
    "open_apk": "Ctrl+O",
    "rerun": "F5",
    "home": "Ctrl+Shift+H",
    "recents": "Ctrl+Shift+R",
    "overlay": "Ctrl+Shift+F12",   # frame age / fps, for development
    # [P3b]: shake F6, rotate F7/F8, Korean input bar Ctrl+T
}
RESERVED_P3B = ("F6", "F7", "F8", "Ctrl+T")

# Keys that belong to the phone while its screen has focus.
PHONE_EDIT_KEYS = ("Ctrl+A", "Ctrl+C", "Ctrl+X", "Ctrl+Z", "Ctrl+V", "Ctrl+Left", "Ctrl+Right", "Ctrl+Shift+Left",
                   "Ctrl+Shift+Right", "Backspace", "Delete", "Return", "Tab", "Left", "Right", "Up", "Down", "Home",
                   "End", "Esc")

# W3C key names the emulator's KeyboardEvent understands (S2/S8)
NAMED_KEYS: dict[Qt.Key, str] = {
    Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Return: "Enter", Qt.Key.Key_Enter: "Enter", Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Delete: "Delete", Qt.Key.Key_Left: "ArrowLeft", Qt.Key.Key_Right: "ArrowRight",
    Qt.Key.Key_Up: "ArrowUp", Qt.Key.Key_Down: "ArrowDown", Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End",
}
CHORD_KEYS: dict[Qt.Key, str] = {Qt.Key.Key_A: "a", Qt.Key.Key_C: "c", Qt.Key.Key_X: "x", Qt.Key.Key_Z: "z"}
