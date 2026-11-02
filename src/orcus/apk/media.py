"""Pictures, sounds and videos dropped on the window go into the phone (plan.md Phase 4 "사진·소리 파일 드롭").

`adb push` into /sdcard/Pictures, Music or Movies is enough: MediaStore lists the file at once, as the FUSE path
indexes it when it is closed (4a device check, Korean names too), so ImagePicker, Player and VideoPlayer find it.
adb only sees the staged copy `<staging>\\media\\<sha256>.<ext>` (§7.2); the phone keeps the dropped name. A staged
copy also has today's date: pushed straight, the PC file's old date sent a photo to the bottom of the picker.
Which folder: contracts.media_folder (the window asks too, while a file is dragged).
"""

import re

NAME_MAX_BYTES = 200   # ext4/f2fs allow 255 bytes a name; Korean takes 3 each
_BAD = re.compile(r'[\x00-\x1f"*/:<>?\\|]')


def phone_name(name: str) -> str:
    """The dropped name, without what the phone's storage refuses, and short enough for it."""
    clean = _BAD.sub("_", name).strip().lstrip(".") or "file"
    stem, dot, ext = clean.rpartition(".")
    if not dot:
        stem, ext = clean, ""
    while len(clean.encode("utf-8")) > NAME_MAX_BYTES and stem:
        stem = stem[:-1]
        clean = f"{stem}.{ext}" if ext else stem
    return clean
