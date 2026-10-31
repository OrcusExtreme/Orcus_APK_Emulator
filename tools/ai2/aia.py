"""App Inventor project files, written the way the IDE and buildserver of appinventor-sources expect them.

    youngandroidproject/project.properties     project settings (buildserver: Project.java)
    src/appinventor/ai_<user>/<Name>/<Screen>.scm   designer JSON
    src/.../<Screen>.bky                        blocks (Blockly XML)
    src/.../<Screen>.yail                       compiled form of both, only in the build zip
    assets/...                                  media

The .aia (IDE import) holds everything except .yail, as the IDE's own export does; the build zip adds the .yail
(the IDE normally generates it in the browser) and, once one exists, the signing keystore.
Versions and the YAIL layout follow appinventor-sources 0712c114 (2026-10-29): YaVersion.java and
blocklyeditor/src/generators/yail.js (getFormYail_, getPropertySettersLines, getPropertyValueString).
"""

import json
import re
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from ai2 import blocks as b

YA_VERSION = 239                 # YaVersion.YOUNG_ANDROID_VERSION
BLOCKS_LANGUAGE_VERSION = 40     # YaVersion.BLOCKS_LANGUAGE_VERSION
RUNTIME_PKG = "com.google.appinventor.components.runtime"
KEYSTORE = "android.keystore"    # YoungAndroidConstants.PROJECT_KEYSTORE_LOCATION

COMPONENT_VERSIONS = {           # YaVersion.<TYPE>_COMPONENT_VERSION
    "AccelerometerSensor": 6, "ActivityStarter": 6, "Ball": 8, "BarcodeScanner": 2, "Button": 8, "Canvas": 15,
    "Clock": 4, "Form": 32, "HorizontalArrangement": 4, "ImageSprite": 11, "Label": 6, "LocationSensor": 5,
    "OrientationSensor": 2, "PasswordTextBox": 8, "Player": 8, "Sound": 4, "SoundRecorder": 2, "SpeechRecognizer": 4,
    "TextBox": 15, "TextToSpeech": 6, "VerticalArrangement": 4, "WebViewer": 11,
}

# YAIL type of every designer property used here (the Java setter's type via ComponentProcessor.javaTypeToYailType).
PROP_TYPES = {
    **dict.fromkeys(("AppName", "Title", "Theme", "ScreenOrientation", "Sizing", "VersionName", "Text", "Image",
                     "Source", "Hint", "HomeUrl", "Language", "Action", "Picture", "AndroidMinSdk"), "text"),
    **dict.fromkeys(("ActionBar", "Scrollable", "ShowListsAsJson", "MultiLine", "NumbersOnly", "UseLegacy"),
                    "boolean"),
    **dict.fromkeys(("Width", "Height", "FontSize", "BackgroundColor", "TextColor", "VersionCode", "TimeInterval",
                     "DistanceInterval", "Sensitivity", "AlignHorizontal", "PaintColor", "LineWidth", "Radius",
                     "X", "Y", "TimerInterval", "Interval"), "number"),
}
ASSET_PROPS = ("Image", "Source", "Picture")
# @DesignerProperty(alwaysSend = true): the YAIL sets these even when the .scm leaves them at the default.
ALWAYS_SEND = {"Form": {"ScreenOrientation": "unspecified", "Sizing": "Responsive", "ShowListsAsJson": "True"}}

FILL_PARENT = "-2"
_INT = re.compile(r"^\s*[-+]?[0-9]+\s*$")
_FLO = re.compile(r"^\s*[-+]?([0-9]*)((\.[0-9]+)|[0-9]\.)\s*$")


@dataclass
class Component:
    type: str
    name: str
    props: dict[str, str] = field(default_factory=dict)
    children: list["Component"] = field(default_factory=list)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Screen:
    name: str
    props: dict[str, str]
    components: list[Component]
    globals: list[b.GlobalDecl] = field(default_factory=list)
    events: list[b.Event] = field(default_factory=list)

    def form(self) -> Component:
        return Component("Form", self.name, self.props, self.components)


@dataclass
class Project:
    name: str
    screens: list[Screen]
    assets: dict[str, bytes] = field(default_factory=dict)
    user: str = "orcus"
    settings: dict[str, str] = field(default_factory=dict)   # extra project.properties entries

    @property
    def package(self) -> str:
        return f"appinventor.ai_{self.user}.{self.name}"

    def src_dir(self) -> str:
        return "src/" + self.package.replace(".", "/")

    def properties(self) -> str:
        app_name = self.screens[0].props.get("AppName", self.name)
        entries = {
            "main": f"{self.package}.Screen1", "name": self.name, "aname": app_name,
            "assets": "../assets", "source": "../src", "build": "../build",
            "versioncode": "1", "versionname": "1.0", "useslocation": "False", "sizing": "Responsive",
            "showlistsasjson": "True", "actionbar": "True", "theme": "AppTheme.Light.DarkActionBar",
            "color.primary": "&HFF3F51B5", "color.primary.dark": "&HFF303F9F", "color.accent": "&HFFFF4081",
            "defaultfilescope": "App",
        } | self.settings
        return "".join(f"{k}={_prop_escape(v)}\n" for k, v in entries.items())

    def files(self, with_yail: bool) -> dict[str, bytes]:
        out = {"youngandroidproject/project.properties": self.properties().encode("ascii")}
        for s in self.screens:
            base = f"{self.src_dir()}/{s.name}"
            out[base + ".scm"] = scm(s).encode("utf-8")
            out[base + ".bky"] = bky(s).encode("utf-8")
            if with_yail:
                out[base + ".yail"] = yail(s, f"{self.package}.{s.name}").encode("utf-8")
        for name, data in self.assets.items():
            out[f"assets/{name}"] = data
        return out

    def write_zip(self, path: Path, *, with_yail: bool, keystore: Path | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.files(with_yail).items():
                z.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
            if keystore is not None:
                z.write(keystore, KEYSTORE)
        return path


def _prop_escape(v: str) -> str:
    """java.util.Properties: non-ASCII as \\uXXXX, so any reader charset works."""
    return "".join(c if " " <= c <= "~" else f"\\u{ord(c):04x}" for c in v)


def _uuid(screen: str, name: str) -> str:
    return "0" if name == "Screen1" else str(zlib.crc32(f"{screen}/{name}".encode()) - 2 ** 31)


# --- .scm --------------------------------------------------------------------------------------------------
def _scm_component(screen: str, c: Component) -> dict:
    d: dict = {"$Name": c.name, "$Type": c.type, "$Version": str(COMPONENT_VERSIONS[c.type])}
    d.update(c.props)
    d["Uuid"] = _uuid(screen, c.name)
    if c.children:
        d["$Components"] = [_scm_component(screen, k) for k in c.children]
    return d


def scm(s: Screen) -> str:
    body = {"authURL": [], "YaVersion": str(YA_VERSION), "Source": "Form",
            "Properties": _scm_component(s.name, s.form())}
    return "#|\n$JSON\n" + json.dumps(body, separators=(",", ":")) + "\n|#"


# --- .bky --------------------------------------------------------------------------------------------------
def bky(s: Screen) -> str:
    b.reset_ids()
    tops = [g.bky(20, 20 + 80 * i) for i, g in enumerate(s.globals)]
    y0 = 40 + 80 * len(s.globals)
    tops += [e.bky(20 + 560 * (i % 2), y0 + 240 * (i // 2)) for i, e in enumerate(s.events)]
    return ('<xml xmlns="https://developers.google.com/blockly/xml">' + "".join(tops)
            + f'<yacodeblocks ya-version="{YA_VERSION}" language-version="{BLOCKS_LANGUAGE_VERSION}">'
              "</yacodeblocks></xml>")


# --- .yail -------------------------------------------------------------------------------------------------
def _value(value: str, ytype: str) -> str:
    """AI.Yail.getPropertyValueString."""
    if ytype == "number":
        if _INT.match(value) or _FLO.match(value):
            return value
        if value.startswith("&H"):
            return "#x" + value[2:]
    elif ytype == "boolean":
        if "False" in value:
            return "#f"
        if "True" in value:
            return "#t"
    return '""' if value in ("", "null") else b.yail_str(value)


def _setters(c: Component) -> list[str]:
    """AI.Yail.getPropertySettersLines: sorted by name, alwaysSend defaults filled in."""
    props = dict(c.props)
    for k, default in ALWAYS_SEND.get(c.type, {}).items():
        props.setdefault(k, default)
    out = []
    for k in sorted(props):
        ytype = PROP_TYPES[k]
        out.append(f"(set-and-coerce-property! '{c.name} '{k} {_value(props[k], ytype)} '{ytype})\n")
    return out


def yail(s: Screen, qualified_name: str) -> str:
    """AI.Yail.getFormYail_ for an APK build."""
    by_comp: dict[str, list[b.Event]] = {}
    for e in s.events:
        by_comp.setdefault(e.comp, []).append(e)
    code = ["#|\n$Source $Yail\n|#\n\n"
            f"(define-form {qualified_name} {s.name} #f)\n(require <com.google.youngandroid.runtime>)\n"]
    code += [g.yail() for g in s.globals]
    code.append(f";;; {s.name}\n")
    code.append("(do-after-form-creation " + " ".join(_setters(s.form())) + ")\n")
    code += [e.yail() for e in by_comp.get(s.name, [])]

    def add(c: Component, parent: str) -> None:
        code.append(f";;; {c.name}\n")
        code.append(f"(add-component {parent} {RUNTIME_PKG}.{c.type} {c.name} ")
        code.extend(_setters(c))
        code.append(")\n")
        code.extend(e.yail() for e in by_comp.get(c.name, []))
        for k in c.children:
            add(k, c.name)

    for c in s.components:
        add(c, s.name)
    code.append("(init-runtime)")
    return "\n".join(code)


# --- consistency checks (no App Inventor needed) -------------------------------------------------------------
def problems(p: Project) -> list[str]:
    """Static checks a build would otherwise only reveal on the device: names, types, references, syntax."""
    import xml.etree.ElementTree as ET

    errs: list[str] = []
    for s in p.screens:
        comps = {c.name: c for c in s.form().walk()}
        if len(comps) != sum(1 for _ in s.form().walk()):
            errs.append(f"{s.name}: duplicate component names")
        for c in comps.values():
            if c.type not in COMPONENT_VERSIONS:
                errs.append(f"{s.name}: unknown component type {c.type}")
            errs += [f"{s.name}.{c.name}: no YAIL type for property {k}" for k in c.props if k not in PROP_TYPES]
        text = yail(s, f"{p.package}.{s.name}")
        refs = set(re.findall(r"'([A-Za-z_][A-Za-z0-9_]*) '[A-Z]", text))
        refs |= {e.comp for e in s.events}
        errs += [f"{s.name}: YAIL refers to unknown component {r}" for r in sorted(refs - comps.keys())]
        declared = {g.name for g in s.globals}
        used = set(re.findall(r"g\$([A-Za-z_][A-Za-z0-9_]*)", text))
        errs += [f"{s.name}: undeclared global {g}" for g in sorted(used - declared)]
        if not _balanced(text):
            errs.append(f"{s.name}: unbalanced parentheses in YAIL")
        try:
            ET.fromstring(bky(s))
        except ET.ParseError as e:
            errs.append(f"{s.name}: .bky is not well-formed XML: {e}")
        json.loads(scm(s).split("\n")[2])
    names = {s.name for s in p.screens}
    errs += [f"assets: {a} is referenced but missing" for s in p.screens for c in s.form().walk()
             for k, a in c.props.items() if k in ASSET_PROPS and a not in p.assets]
    if "Screen1" not in names:
        errs.append("no Screen1")
    return errs


def _balanced(text: str) -> bool:
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == ";":
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("#|", i):
            i = text.index("|#", i) + 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0
