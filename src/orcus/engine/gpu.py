"""Renderer check (ARCHITECTURE.md §5.6): host GPU or a software fallback, read from SurfaceFlinger."""

SOFTWARE_MARKERS = ("SwiftShader", "lavapipe", "llvmpipe")
RENDERER_CMD = "dumpsys SurfaceFlinger | grep -m1 GLES"


def renderer(dumpsys_line: str) -> str:
    """'GLES: <vendor>, <renderer>, <version>' -> the text after 'GLES:' (empty when absent)."""
    line = dumpsys_line.strip().splitlines()[0] if dumpsys_line.strip() else ""
    return line.split("GLES:", 1)[1].strip() if "GLES:" in line else line


def is_software(renderer_text: str) -> bool:
    return any(m.lower() in renderer_text.lower() for m in SOFTWARE_MARKERS)
