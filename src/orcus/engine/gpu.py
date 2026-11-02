"""Renderer check (ARCHITECTURE.md §5.6): host GPU or a software fallback, read from SurfaceFlinger.

API 36's dumpsys has a '------------RE GLES (Ganesh)------------' section header before the 'GLES: <vendor>,
<renderer>, <version>' line: a plain `grep -m1 GLES` returns the header, and a software fallback in a host mode
goes unseen. Only the line with 'GLES: ' counts.
"""

SOFTWARE_MARKERS = ("SwiftShader", "lavapipe", "llvmpipe")
RENDERER_CMD = "dumpsys SurfaceFlinger | grep -m1 'GLES: '"


def renderer(dumpsys_text: str) -> str:
    """The text after 'GLES:' on the first line that has it; empty when there is none."""
    for line in dumpsys_text.splitlines():
        if "GLES:" in line:
            return line.split("GLES:", 1)[1].strip()
    return ""


def is_software(renderer_text: str) -> bool:
    return any(m.lower() in renderer_text.lower() for m in SOFTWARE_MARKERS)
