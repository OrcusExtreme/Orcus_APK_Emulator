import dataclasses

import pytest

from orcus.common import pins
from orcus.contracts import ErrorCode, OrcusFailure
from orcus.setup import repo_index

PT = pins.PLATFORM_TOOLS
NS = 'xmlns:sdk="http://schemas.android.com/sdk/android/repo/repository2/04"'


def index_xml(*, pt_sha1: str = PT.sha1, pt_channel: str = "channel-0", emulator_build: str = "16740729") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<sdk:sdk-repository {NS}>
  <license id="android-sdk-license" type="text">Terms and Conditions
  This is the Android SDK License Agreement
</license>
  <channel id="channel-0">stable</channel>
  <channel id="channel-2">dev</channel>
  <remotePackage path="platform-tools">
    <revision><major>37</major><minor>0</minor><micro>1</micro></revision>
    <uses-license ref="android-sdk-license"/>
    <channelRef ref="{pt_channel}"/>
    <archives>
      <archive><complete><size>9054187</size><checksum type="sha1">aa</checksum><url>platform-tools_r37.0.1-linux.zip</url></complete><host-os>linux</host-os></archive>
      <archive><complete><size>{PT.size}</size><checksum type="sha1">{pt_sha1}</checksum><url>{PT.filename}</url></complete><host-os>windows</host-os></archive>
    </archives>
  </remotePackage>
  <remotePackage path="emulator">
    <revision><major>37</major><minor>2</minor><micro>13</micro></revision>
    <uses-license ref="android-sdk-license"/>
    <channelRef ref="channel-0"/>
    <archives>
      <archive><complete><size>455344678</size><checksum type="sha1">4d72</checksum><url>emulator-windows_x64-{emulator_build}.zip</url></complete><host-os>windows</host-os><host-arch>x64</host-arch></archive>
    </archives>
  </remotePackage>
</sdk:sdk-repository>
""".encode()


def parse(data: bytes) -> repo_index.RepoIndex:
    return repo_index.parse_index(data, "test", pins.SDK_REPO)


def test_parse_packages_channels_licenses() -> None:
    idx = parse(index_xml())
    (pt,) = idx.find("platform-tools")
    assert pt.revision == "37.0.1" and pt.channel == "stable" and pt.license_ref == "android-sdk-license"
    assert pt.archives[1].url == pins.SDK_REPO + PT.filename and pt.archives[1].host_os == "windows"
    text = idx.licenses["android-sdk-license"]
    assert text.startswith("Terms and Conditions") and not text.endswith(" ")


def test_resolve_pinned_package_in_index() -> None:
    r = repo_index.resolve(PT, parse(index_xml()))
    assert r.in_index and r.url == PT.url and r.license_id == "android-sdk-license"


def test_resolve_refuses_a_different_checksum() -> None:
    with pytest.raises(OrcusFailure) as e:
        repo_index.resolve(PT, parse(index_xml(pt_sha1="0" * 40)))
    assert e.value.error.code is ErrorCode.SETUP_CHECKSUM


def test_resolve_refuses_non_stable() -> None:
    with pytest.raises(OrcusFailure) as e:
        repo_index.resolve(PT, parse(index_xml(pt_channel="channel-2")))
    assert e.value.error.code is ErrorCode.SETUP_LICENSE_NOT_STABLE


def test_pin_missing_from_index_uses_fixed_url() -> None:
    r = repo_index.resolve(pins.EMULATOR, parse(index_xml()))
    assert not r.in_index and r.url == pins.EMULATOR.url and r.sha1 == pins.EMULATOR.sha1


def test_license_texts_group_packages() -> None:
    idx = parse(index_xml())
    resolved = [repo_index.resolve(a, idx) for a in (PT, pins.EMULATOR)]
    (lt,) = repo_index.license_texts(resolved, [idx])
    assert lt.license_id == "android-sdk-license"
    assert lt.packages == ("platform-tools", "emulator")
    assert lt.sha256 == repo_index.license_sha256(lt.text)


def test_license_hash_ignores_line_endings_and_trailing_spaces() -> None:
    assert repo_index.license_sha256("a  \r\nb\r\n") == repo_index.license_sha256("a\nb")


def test_missing_license_text_is_an_error() -> None:
    idx = dataclasses.replace(parse(index_xml()), licenses={})
    with pytest.raises(OrcusFailure):
        repo_index.license_texts([repo_index.resolve(PT, parse(index_xml()))], [idx])


def test_fetch_first_falls_back() -> None:
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith("2-4.xml"):
            raise OSError("404")
        return index_xml()
    idx = repo_index.fetch_first(pins.REPO_INDEX_URLS, fetch)
    assert idx.source.endswith("2-3.xml") and len(calls) == 2


@pytest.mark.network
def test_live_index_matches_pins() -> None:
    repo = repo_index.fetch_first(pins.REPO_INDEX_URLS)
    sysimg = repo_index.fetch_first(pins.SYSIMG_INDEX_URLS)
    resolved = [repo_index.resolve(a, sysimg if a is pins.SYSTEM_IMAGE else repo) for a in pins.ARCHIVES]
    assert {r.license_id for r in resolved} == {"android-sdk-license"}
    assert [r.in_index for r in resolved] == [True, False, True]   # 37.2.12 left the index on 2026-10-29
