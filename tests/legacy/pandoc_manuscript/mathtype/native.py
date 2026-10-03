"""Call the Rust converters in memory through their versioned C ABI."""

from __future__ import annotations

import ctypes
import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

from ..runtime.logging import log_debug, log_info
from ..runtime.resources import package_resource_path, source_tree_root

_LOAD_LOCK = RLock()


def library_name(project: str) -> str:
    """Return the platform's Cargo cdylib filename."""
    name = project.replace("-", "_")
    if sys.platform == "win32":
        return f"{name}.dll"
    return f"lib{name}.{'dylib' if sys.platform == 'darwin' else 'so'}"


def packaged_library(project: str) -> Path:
    """Locate the shared library included in a platform wheel."""
    return package_resource_path(Path("mathtype/bin") / library_name(project))


def library_path(project: str) -> Path:
    """Resolve the source release library or installed platform library without building."""
    root = source_tree_root()
    if root is not None:
        return root / "scripts" / project / "target/release" / library_name(project)
    return packaged_library(project)


class NativeConverter:
    """Own a loaded library and copy each response before Rust releases it."""

    def __init__(self, project: str, path: Path) -> None:
        """Bind explicit pointer types to avoid address truncation on 64-bit hosts."""
        self.library = ctypes.CDLL(str(path.resolve()))
        prefix = project.replace("-", "_")
        self.convert = getattr(self.library, f"{prefix}_convert_v1")
        self.convert.argtypes = [ctypes.c_char_p]
        self.convert.restype = ctypes.c_void_p
        self.free = getattr(self.library, f"{prefix}_free_v1")
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = None
        log_debug(f"[mathtype] loaded native library: {path}")

    def call(self, **request: Any) -> dict[str, Any]:
        """Return artifacts or raise a recoverable Rust conversion error."""
        pointer = self.convert(json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if not pointer:
            raise RuntimeError("Native converter returned a null response")
        try:
            response = json.loads(ctypes.string_at(pointer))
        finally:
            self.free(pointer)
        if "error" in response:
            raise RuntimeError(response["error"])
        result = response["result"]
        for key in ("ole", "mtef", "wmf"):
            if key in result:
                result[key] = bytes.fromhex(result[key])
        return result


@lru_cache(maxsize=2)
def _load_converter(project: str) -> NativeConverter:
    """Build source libraries once and require the native library for every installation."""
    path = library_path(project)
    root = source_tree_root()
    if root is not None:
        manifest = root / "scripts" / project / "Cargo.toml"
        if manifest.exists() and shutil.which("cargo"):
            log_info(f"[mathtype] checking {project} native library with cargo")
            subprocess.run(
                [
                    "cargo", "rustc", "--crate-type", "cdylib", "--manifest-path", str(manifest),
                    "--lib", "--features", "ffi", "--release",
                ],
                cwd=root, check=True,
            )
    if not path.is_file():
        raise FileNotFoundError(
            f"{project} native library is missing: {path}. Install a platform wheel that bundles it, "
            "or build the source library with Cargo and --features ffi."
        )
    return NativeConverter(project, path)


def get_converter(project: str) -> NativeConverter:
    """Serialize first loads so concurrent calls cannot rebuild a loaded Windows DLL."""
    with _LOAD_LOCK:
        return _load_converter(project)


def latex_to_equation(latex: str, *, prefs_file: str | Path | None = None) -> dict[str, Any]:
    """Return OLE/MTEF bytes and normalized LaTeX without temporary input files."""
    converter = get_converter("mathtype-rust")
    return converter.call(latex=latex, prefs_file=str(prefs_file) if prefs_file is not None else None)


def ole_to_latex(ole: bytes, *, mode: str = "auto") -> str:
    """Recover TeX from OLE bytes in process, falling back to MTEF structure."""
    return get_converter("mathtype-rust").call(operation="decode_ole", ole=ole.hex(), mode=mode)["latex"]


def render_latex_to_wmf(
    latex: str, *, svg_backend: str = "typst", math_style: str = "display",
    font_size_pt: float = 12.0, math_font: str = "XITS Math",
) -> dict[str, Any]:
    """Return WMF bytes, SVG text, and the CLI-compatible metadata JSON string."""
    converter = get_converter("mathtype-rust")
    return converter.call(
        operation="render_wmf",
        latex=latex, svg_backend=svg_backend, math_style=math_style,
        font_size_pt=font_size_pt, math_font=math_font,
    )
