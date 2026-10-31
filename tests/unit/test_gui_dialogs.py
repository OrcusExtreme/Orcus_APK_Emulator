"""gui/dialogs.keeping puts a registry key back after a Qt file dialog wrote it (ARCHITECTURE.md §11.5).
The tests write under a throwaway HKCU\\Software\\OrcusTest-<random> key and remove it."""

import uuid
import winreg
from collections.abc import Iterator

import pytest

from orcus.gui import dialogs

HKCU = winreg.HKEY_CURRENT_USER


def _delete_tree(key: str) -> None:
    try:
        with winreg.OpenKey(HKCU, key) as k:
            subs = [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except OSError:
        return
    for sub in subs:
        _delete_tree(f"{key}\\{sub}")
    winreg.DeleteKey(HKCU, key)


@pytest.fixture
def root() -> Iterator[str]:
    name = f"Software\\OrcusTest-{uuid.uuid4().hex[:8]}"
    yield name
    _delete_tree(name)


def _write(key: str, **values: str) -> None:
    with winreg.CreateKey(HKCU, key) as k:
        for name, data in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, data)


def _read(key: str) -> dict[str, object] | None:
    try:
        with winreg.OpenKey(HKCU, key) as k:
            found = [winreg.EnumValue(k, i) for i in range(winreg.QueryInfoKey(k)[1])]
    except FileNotFoundError:
        return None
    return {name: data for name, data, _kind in found}


def test_a_key_the_dialog_created_goes_with_the_parents_it_brought(root: str) -> None:
    key = f"{root}\\OrganizationDefaults\\FileDialog"

    def dialog() -> str:
        _write(key, lastVisited="file:///D:/OrcusDev/fixtures", viewMode="Detail")
        return "HelloPurr.apk"
    assert dialogs.keeping(key, dialog) == "HelloPurr.apk"
    assert _read(root) is None


def test_old_values_come_back_exactly(root: str) -> None:
    key = f"{root}\\OrganizationDefaults\\FileDialog"
    _write(key, lastVisited="file:///C:/before", viewMode="Detail")

    def dialog() -> None:
        _write(key, lastVisited="file:///D:/after", qtVersion="6.12.0")
        with winreg.OpenKey(HKCU, key, 0, winreg.KEY_ALL_ACCESS) as k:
            winreg.DeleteValue(k, "viewMode")
    dialogs.keeping(key, dialog)
    assert _read(key) == {"lastVisited": "file:///C:/before", "viewMode": "Detail"}


def test_a_parent_another_app_writes_to_meanwhile_stays(root: str) -> None:
    key = f"{root}\\OrganizationDefaults\\FileDialog"

    def dialog() -> None:
        _write(key, lastVisited="x")
        _write(f"{root}\\OtherApp", setting="1")
    dialogs.keeping(key, dialog)
    assert _read(f"{root}\\OrganizationDefaults") is None
    assert _read(f"{root}\\OtherApp") == {"setting": "1"}


def test_the_key_is_put_back_when_the_dialog_raises(root: str) -> None:
    key = f"{root}\\OrganizationDefaults\\FileDialog"

    def dialog() -> None:
        _write(key, lastVisited="x")
        raise RuntimeError("dialog failed")
    with pytest.raises(RuntimeError):
        dialogs.keeping(key, dialog)
    assert _read(root) is None
