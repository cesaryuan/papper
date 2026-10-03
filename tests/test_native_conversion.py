"""Exercise the real C ABI against CLI artifacts when native builds are available."""

import json
import ctypes
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class NativeConverter:
    """Call the Rust library's public C ABI directly, without the archived Python adapter."""

    def __init__(self, path: Path) -> None:
        """Load the native library and declare its response ownership boundary."""
        self.library = ctypes.CDLL(str(path))
        self.convert = self.library.mathtype_rust_convert_v1
        self.convert.argtypes = [ctypes.c_char_p]
        self.convert.restype = ctypes.c_void_p
        self.free = self.library.mathtype_rust_free_v1
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = None

    def call(self, **request) -> dict:
        """Copy Rust artifacts before releasing each owned native response."""
        pointer = self.convert(json.dumps(request).encode("utf-8"))
        assert pointer
        try:
            response = json.loads(ctypes.string_at(pointer))
        finally:
            self.free(pointer)
        if "error" in response:
            raise RuntimeError(response["error"])
        result = response["result"]
        for name in ("ole", "mtef", "wmf"):
            if name in result:
                result[name] = bytes.fromhex(result[name])
        return result


@pytest.fixture
def converters():
    """Load prebuilt libraries without triggering Cargo in ordinary Python tests."""
    result = {}
    for project in ("mathtype-rust",):
        library = ("mathtype_rust.dll" if os.name == "nt" else
                   "libmathtype_rust.dylib" if sys.platform == "darwin" else "libmathtype_rust.so")
        path = ROOT / "scripts" / project / "target/release" / library
        suffix = ".exe" if os.name == "nt" else ""
        required = [path, ROOT / f"scripts/{project}/target/release/{project}{suffix}",
                    ROOT / f"scripts/latex2wmf/target/release/latex2wmf{suffix}"]
        if not all(candidate.is_file() for candidate in required):
            pytest.skip("Build the mathtype-rust release cdylib and both reference CLIs first")
        result[project] = NativeConverter(path)
    return result


@pytest.mark.parametrize("latex", [r"\frac{\alpha_1}{2}", r"\mathbf{x}+\sqrt{y}"])
def test_equation_matches_cli(converters, tmp_path, latex):
    """Keep byte-exact OLE/MTEF compatibility, including preference-file handling."""
    prefs = ROOT / "src/pandoc_manuscript/mathtype/Times+Symbol 12.eqp"
    result = converters["mathtype-rust"].call(latex=latex, prefs_file=str(prefs))
    executable = ROOT / "scripts/mathtype-rust/target/release" / ("mathtype-rust.exe" if os.name == "nt" else "mathtype-rust")
    subprocess.run([str(executable), "--latex", latex, "--output", str(tmp_path / "eq.ole"),
                    "--mtef-output", str(tmp_path / "eq.mtef"), "--prefs-file", str(prefs)], check=True)
    assert result["ole"] == (tmp_path / "eq.ole").read_bytes()
    assert result["mtef"] == (tmp_path / "eq.mtef").read_bytes()


@pytest.mark.parametrize("backend,style", [("typst", "inline"), ("ratex", "display")])
def test_preview_matches_cli(converters, tmp_path, backend, style):
    """Preserve vector artifacts and baseline metadata for both rendering engines."""
    latex = r"\frac{x_1}{2}"
    result = converters["mathtype-rust"].call(operation="render_wmf", latex=latex, svg_backend=backend, math_style=style, font_size_pt=10.5)
    executable = ROOT / "scripts/latex2wmf/target/release" / ("latex2wmf.exe" if os.name == "nt" else "latex2wmf")
    subprocess.run([str(executable), "--latex", latex, "--output", str(tmp_path / "eq.wmf"),
                    "--metadata-output", str(tmp_path / "eq.json"), "--svg-output", str(tmp_path / "eq.svg"),
                    "--svg-backend", backend, "--math-style", style, "--font-size", "10.5"], check=True)
    assert result["wmf"] == (tmp_path / "eq.wmf").read_bytes()
    assert result["svg"] == (tmp_path / "eq.svg").read_text(encoding="utf-8")
    assert json.loads(result["metadata_json"]) == json.loads((tmp_path / "eq.json").read_text())


def test_cached_typst_keeps_each_requests_layout(converters, tmp_path):
    """Compare concurrent mixed layouts with independent CLI renders to catch shared-input leaks."""
    converter = converters["mathtype-rust"]
    executable = ROOT / "scripts/latex2wmf/target/release" / ("latex2wmf.exe" if os.name == "nt" else "latex2wmf")
    contexts = [
        dict(latex=r"\frac{x_1}{2}", math_style="inline", font_size_pt=10.5),
        dict(latex=r"\frac{x_2}{3}", math_style="display", font_size_pt=16.0),
        dict(latex=r"\text{中文}+x", math_style="inline", font_size_pt=12.0),
        dict(latex=r"\sqrt{y}", math_style="display", font_size_pt=9.0),
    ]
    expected = []
    for index, context in enumerate(contexts):
        output = tmp_path / f"{index}.wmf"
        metadata = tmp_path / f"{index}.json"
        subprocess.run([
            str(executable), "--latex", context["latex"], "--svg-backend", "typst",
            "--math-style", context["math_style"], "--font-size", str(context["font_size_pt"]),
            "--output", str(output), "--metadata-output", str(metadata),
        ], check=True, capture_output=True)
        expected.append((output.read_bytes(), json.loads(metadata.read_text(encoding="utf-8"))))
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(converter.call, operation="render_wmf", **context) for context in contexts * 2]
        for index, future in enumerate(futures):
            result = future.result()
            assert (result["wmf"], json.loads(result["metadata_json"])) == expected[index % len(contexts)]


def test_cached_typst_observes_font_file_replacement(converters, tmp_path):
    """A cached font must not conceal replacement, deletion, or a different requested family."""
    converter = converters["mathtype-rust"]
    original = (ROOT / "scripts/latex2wmf/assets/fonts/XITSMath-Regular.otf").read_bytes()
    font = tmp_path / "数学字体.otf"
    font.write_bytes(original)
    request = dict(operation="render_wmf", latex=r"\frac{x}{2}", math_font=str(font))
    expected = converter.call(**request)["wmf"]
    font.write_bytes(b"not an OpenType math font")
    with pytest.raises(RuntimeError, match="no OpenType math font"):
        converter.call(**request)
    font.unlink()
    with pytest.raises(RuntimeError, match="failed to read math font"):
        converter.call(**request)
    with pytest.raises(RuntimeError, match="not installed"):
        converter.call(operation="render_wmf", latex="x", math_font="Papper nonexistent math font 20260908")
    font.write_bytes(original)
    assert converter.call(**request)["wmf"] == expected
