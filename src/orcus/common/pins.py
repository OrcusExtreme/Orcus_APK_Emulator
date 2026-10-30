"""Pinned SDK components: the only place that knows URLs, sizes, SHA-1s and the emulator build.

Values were read from repository2-4.xml / sys-img2-5.xml on 2026-10-30. The emulator build
16428233 (37.2.12) has since left the index (37.2.13 / 16740729 is the new stable); setup then
downloads it from the pinned URL and verifies the pinned SHA-1 (ARCHITECTURE.md §11.6 step 4).
Upgrading means changing this file and passing the smoke gate, never a silent switch.
"""

from dataclasses import dataclass

SDK_REPO = "https://dl.google.com/android/repository/"
REPO_INDEX_URLS = (SDK_REPO + "repository2-4.xml", SDK_REPO + "repository2-3.xml")
SYSIMG_INDEX_URLS = (
    SDK_REPO + "sys-img/google_apis/sys-img2-5.xml",
    SDK_REPO + "sys-img/google_apis/sys-img2-4.xml",
    SDK_REPO + "sys-img/google_apis/sys-img2-3.xml",
)
STABLE_CHANNEL = "stable"
EMULATOR_BUILD = "16428233"
PROTO_DIR_NAME = EMULATOR_BUILD   # src/orcus/emugrpc/protos/<build>/
SYSTEM_IMAGE_API = 36
SYSTEM_IMAGE_TAG = "google_apis"


@dataclass(frozen=True)
class Archive:
    key: str                     # short name used in state files and messages
    package_path: str            # sdkmanager path in the repository index
    revision: str
    url: str
    size: int
    sha1: str
    license_id: str              # used when the package is no longer in the index
    install_dir: str             # relative to sdk\
    zip_top: str                 # top-level folder inside the zip
    required: tuple[str, ...]    # file names (or globs) that must exist under install_dir
    optional: tuple[str, ...]    # missing ones only produce a warning
    in_index_host: str | None    # host-os of the archive in the index (None = any)

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]


EMULATOR = Archive(
    key="emulator",
    package_path="emulator",
    revision="37.2.12",
    url=SDK_REPO + f"emulator-windows_x64-{EMULATOR_BUILD}.zip",
    size=455_342_868,
    sha1="488ed747e82de7e9bb5247becd1ac043c7e5e85d",
    license_id="android-sdk-license",
    install_dir="emulator",
    zip_top="emulator",
    required=("source.properties", "emulator.exe", "emulator-check.exe", "qemu-system-x86_64-headless.exe",
              "qemu-system-x86_64.exe", "emulator_access.json", "*.proto"),
    optional=("netsimd.exe", "crashpad_handler.exe"),
    in_index_host="windows",
)

PLATFORM_TOOLS = Archive(
    key="platform-tools",
    package_path="platform-tools",
    revision="37.0.1",
    url=SDK_REPO + "platform-tools_r37.0.1-win.zip",
    size=8_044_989,
    sha1="e03e78b1d80b396f1c3358e31251cb31740e1110",
    license_id="android-sdk-license",
    install_dir="platform-tools",
    zip_top="platform-tools",
    required=("source.properties", "adb.exe", "AdbWinApi.dll"),
    optional=(),
    in_index_host="windows",
)

SYSTEM_IMAGE = Archive(
    key="system-image",
    package_path=f"system-images;android-{SYSTEM_IMAGE_API};{SYSTEM_IMAGE_TAG};x86_64",
    revision="7",
    url=SDK_REPO + "sys-img/google_apis/x86_64-36_r07.zip",
    size=1_895_447_397,
    sha1="c6bf44bdcd885bb902b4ba752d111a073ad7a817",
    license_id="android-sdk-license",
    install_dir=rf"system-images\android-{SYSTEM_IMAGE_API}\{SYSTEM_IMAGE_TAG}\x86_64",
    zip_top="x86_64",
    required=("source.properties", "system.img"),
    optional=("kernel-ranchu", "ramdisk.img", "vendor.img"),
    in_index_host=None,
)

ARCHIVES = (PLATFORM_TOOLS, EMULATOR, SYSTEM_IMAGE)


def revision_tuple(text: str) -> tuple[int, ...]:
    """'7' == '7.0' == '7.0.0'; non-numeric parts count as 0."""
    parts = [int(p) if p.isdigit() else 0 for p in text.strip().split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)
