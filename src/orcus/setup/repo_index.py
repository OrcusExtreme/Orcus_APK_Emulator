"""SDK repository index: package entries, channels and license texts (repository2-4 / sys-img2-5)."""

import hashlib
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from orcus.common import messages_ko
from orcus.common.pins import STABLE_CHANNEL, Archive, revision_tuple
from orcus.contracts import ErrorCode, LicenseText, OrcusFailure

type Fetch = Callable[[str], bytes]


@dataclass(frozen=True)
class RemoteArchive:
    host_os: str | None
    url: str
    size: int
    sha1: str


@dataclass(frozen=True)
class RemotePackage:
    path: str
    revision: str
    license_ref: str | None
    channel: str | None   # channel name ("stable", "beta", ...)
    archives: tuple[RemoteArchive, ...]


@dataclass(frozen=True)
class RepoIndex:
    source: str
    packages: tuple[RemotePackage, ...]
    licenses: dict[str, str]

    def find(self, path: str) -> list[RemotePackage]:
        return [p for p in self.packages if p.path == path]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def normalize_license(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def license_sha256(text: str) -> str:
    return hashlib.sha256(normalize_license(text).encode("utf-8")).hexdigest()


def parse_index(data: bytes, source: str, base_url: str) -> RepoIndex:
    root = ET.fromstring(data)
    channels = {c.get("id", ""): _text(c) for c in root if _local(c.tag) == "channel"}
    licenses = {lic.get("id", ""): normalize_license(lic.text or "") for lic in root if _local(lic.tag) == "license"}
    packages: list[RemotePackage] = []
    for pkg in root:
        if _local(pkg.tag) != "remotePackage":
            continue
        rev = _child(pkg, "revision")
        revision = ".".join(_text(_child(rev, part)) for part in ("major", "minor", "micro")
                            if rev is not None and _child(rev, part) is not None) if rev is not None else ""
        lic = _child(pkg, "uses-license")
        chan = _child(pkg, "channelRef")
        archives: list[RemoteArchive] = []
        container = _child(pkg, "archives")
        for arch in (container if container is not None else []):
            complete = _child(arch, "complete")
            if complete is None:
                continue
            url = _text(_child(complete, "url"))
            host = _text(_child(arch, "host-os")) or None
            archives.append(RemoteArchive(host, url if "://" in url else base_url + url,
                                          int(_text(_child(complete, "size")) or 0),
                                          _text(_child(complete, "checksum")).lower()))
        packages.append(RemotePackage(
            path=pkg.get("path", ""), revision=revision,
            license_ref=lic.get("ref") if lic is not None else None,
            channel=channels.get(chan.get("ref", "")) if chan is not None else None,
            archives=tuple(archives)))
    return RepoIndex(source, tuple(packages), licenses)


def http_fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def fetch_first(urls: Sequence[str], fetch: Fetch = http_fetch) -> RepoIndex:
    """First index that downloads and parses (repository2-4, then -3 ...)."""
    errors: list[str] = []
    for url in urls:
        try:
            return parse_index(fetch(url), url, url.rsplit("/", 1)[0] + "/")
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError) as e:
            errors.append(f"{url}: {e}")
    raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_NETWORK, "; ".join(errors)))


@dataclass(frozen=True)
class Resolved:
    archive: Archive
    url: str
    size: int
    sha1: str
    license_id: str
    in_index: bool


def resolve(pin: Archive, index: RepoIndex) -> Resolved:
    """Match the pin against the index. Never upgrades: a different size/SHA-1 is an error.

    A pin that left the index (as 37.2.12 did, S0a) is downloaded from its fixed URL.
    """
    for pkg in index.find(pin.package_path):
        if revision_tuple(pkg.revision) != revision_tuple(pin.revision):
            continue
        for arch in pkg.archives:
            if arch.url.rsplit("/", 1)[-1] != pin.filename:
                continue
            if arch.size != pin.size or arch.sha1 != pin.sha1:
                raise OrcusFailure(messages_ko.error(
                    ErrorCode.SETUP_CHECKSUM, f"{pin.filename}: index {arch.size}/{arch.sha1} != pin {pin.size}/{pin.sha1}"))
            if pkg.channel != STABLE_CHANNEL or pkg.license_ref is None:
                raise OrcusFailure(messages_ko.error(
                    ErrorCode.SETUP_LICENSE_NOT_STABLE, f"{pin.package_path}: channel={pkg.channel} license={pkg.license_ref}"))
            return Resolved(pin, arch.url, arch.size, arch.sha1, pkg.license_ref, True)
    return Resolved(pin, pin.url, pin.size, pin.sha1, pin.license_id, False)


def license_texts(resolved: Sequence[Resolved], indexes: Sequence[RepoIndex]) -> list[LicenseText]:
    """One LicenseText per license id the packages need, with the packages it covers."""
    texts: dict[str, str] = {}
    for idx in indexes:
        for lic_id, text in idx.licenses.items():
            texts.setdefault(lic_id, text)
    out: list[LicenseText] = []
    for lic_id in sorted({r.license_id for r in resolved}):
        if lic_id not in texts:
            raise OrcusFailure(messages_ko.error(ErrorCode.SETUP_LICENSE_NOT_STABLE, f"license text {lic_id} missing"))
        text = texts[lic_id]
        packages = tuple(r.archive.package_path for r in resolved if r.license_id == lic_id)
        out.append(LicenseText(lic_id, text, license_sha256(text), packages))
    return out
