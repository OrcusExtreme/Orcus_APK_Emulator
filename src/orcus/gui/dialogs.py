"""File dialogs that leave no Qt settings behind (ARCHITECTURE.md §11.5).

Every QFileDialog, the native one too, saves its state (last folder, history, view mode) to
HKCU\\Software\\QtProject\\OrganizationDefaults\\FileDialog when it closes. §11.5 allows nothing there, so each dialog
here puts that key back as it found it: the old values, or no key (and none of the parents it brought along).
"""

import winreg
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QFileDialog, QWidget

from orcus.common import log

QT_KEY = r"Software\QtProject\OrganizationDefaults\FileDialog"
_HKCU = winreg.HKEY_CURRENT_USER
_log = log.get("gui.dialogs")

type Values = list[tuple[str, Any, int]]


def open_file(parent: QWidget, caption: str, folder: str, filters: str) -> str:
    return keeping(QT_KEY, lambda: QFileDialog.getOpenFileName(parent, caption, folder, filters))[0]


def save_file(parent: QWidget, caption: str, path: str, filters: str) -> str:
    return keeping(QT_KEY, lambda: QFileDialog.getSaveFileName(parent, caption, path, filters))[0]


def keeping[T](key: str, call: Callable[[], T]) -> T:
    """Run `call`, then put HKCU\\`key` back: its old values, or gone together with the parents it created."""
    parts = key.split("\\")
    chain = ["\\".join(parts[:n]) for n in range(len(parts), 1, -1)]   # the key, then its parents below Software
    existed = {k for k in chain if _exists(k)}
    old = _values(key) if key in existed else []
    try:
        return call()
    finally:
        try:
            _put_back(chain, existed, old)
        except OSError as e:
            _log.warning("could not put %s back: %s", key, e)


def _exists(key: str) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(_HKCU, key))
        return True
    except OSError:
        return False


def _values(key: str) -> Values:
    with winreg.OpenKey(_HKCU, key) as k:
        return _enum(k)


def _enum(k: winreg.HKEYType) -> Values:
    out: Values = []
    i = 0
    while True:
        try:
            out.append(winreg.EnumValue(k, i))
        except OSError:
            return out
        i += 1


def _put_back(chain: list[str], existed: set[str], old: Values) -> None:
    if chain[0] in existed:
        with winreg.OpenKey(_HKCU, chain[0], 0, winreg.KEY_ALL_ACCESS) as k:
            keep = {name for name, _data, _kind in old}
            for name, _data, _kind in _enum(k):
                if name not in keep:
                    winreg.DeleteValue(k, name)
            for name, data, kind in old:
                winreg.SetValueEx(k, name, 0, kind, data)
    for key in chain:   # innermost first; DeleteKey refuses a key with subkeys, so another app's parent stays
        if key not in existed:
            try:
                winreg.DeleteKey(_HKCU, key)
            except OSError:
                pass
