from pathlib import Path
import struct
import subprocess
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.mathtype import ole_parts
from pandoc_manuscript.mathtype import marked_docx
from pandoc_manuscript.mathtype import docx_ole
from pandoc_manuscript.mathtype import convert_marked_docx as convert_marked_docx_module
from pandoc_manuscript.mathtype.ole_parts import decode_process_output
from pandoc_manuscript.runtime.metadata import PmtSettings


def test_missing_helper_is_not_built_during_conversion(monkeypatch, tmp_path) -> None:
    """Require a prebuilt helper even when dotnet is available at runtime."""
    missing_helper = tmp_path / "MathTypeOleHelper.exe"
    monkeypatch.setattr(ole_parts, "HELPER_EXE", missing_helper)
    monkeypatch.setattr(ole_parts.shutil, "which", lambda name: "dotnet.exe")

    with pytest.raises(FileNotFoundError, match="prebuilt|bundles it|build the helper"):
        ole_parts.require_helper_executable()


def test_auto_native_digest_does_not_build_rust_fallbacks(monkeypatch, tmp_path) -> None:
    """Keep a successful auto set-data build from compiling unused Rust libraries."""
    monkeypatch.setattr(ole_parts, "MATHTYPE_RUST_LIBRARY", tmp_path / "missing-rust.dll")
    monkeypatch.setattr(ole_parts.native, "get_converter", lambda name: pytest.fail("digest must not load native libraries"))
    assert ole_parts.native_library_digest_for_method("auto") is None


def test_decode_process_output_falls_back_for_localized_helper_errors() -> None:
    """Preserve Chinese stderr from older helpers that use a Windows code page."""
    message = "[ole-helper] 找不到文件\n"

    assert decode_process_output(message.encode("gb18030")) == message


def minimal_wmf(include_window_mapping: bool = True) -> bytes:
    """Build a tiny placeable WMF for preview validation tests."""
    records = []
    if include_window_mapping:
        records.append(struct.pack("<IHhh", 5, ole_parts.WMF_META_SETWINDOWORG, 0, 0))
        records.append(struct.pack("<IHhh", 5, ole_parts.WMF_META_SETWINDOWEXT, 448, 832))
    records.append(struct.pack("<IH", 3, ole_parts.WMF_META_EOF))
    record_bytes = b"".join(records)
    standard_header = struct.pack(
        "<HHHIHIH",
        1,
        9,
        0x0300,
        (ole_parts.WMF_HEADER_SIZE + len(record_bytes)) // 2,
        0,
        5,
        0,
    )
    return ole_parts.PLACEABLE_WMF_KEY_BYTES + bytes(18) + standard_header + record_bytes


def test_wmf_preview_validation_requires_window_mapping() -> None:
    """Reject SDK-style WMFs that Word displays but exports as blank PDFs."""
    assert ole_parts.has_placeable_wmf_header(minimal_wmf())
    assert ole_parts.wmf_has_window_mapping(minimal_wmf())
    assert not ole_parts.wmf_has_window_mapping(minimal_wmf(include_window_mapping=False))


def test_marked_docx_preserves_inline_formula_context(tmp_path) -> None:
    """Keep inline/display marker context when creating generation requests."""
    source = tmp_path / "marked.docx"
    document_xml = f"""
    <w:document xmlns:w="{marked_docx.NS['w']}" xmlns:m="{marked_docx.NS['m']}">
      <w:body><w:p>
        <w:r><w:rPr><w:vanish/></w:rPr><w:t>MTLATEX:inline:x_1</w:t></w:r>
        <m:oMath><m:r><m:t>x_1</m:t></m:r></m:oMath>
      </w:p></w:body>
    </w:document>
    """.strip()
    styles_xml = f"""
    <w:styles xmlns:w="{marked_docx.NS['w']}">
      <w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults>
    </w:styles>
    """.strip()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)

    requests = marked_docx.extract_marked_equation_requests(source)

    assert requests == [ole_parts.EquationRequest(latex="x_1", font_size_pt=12.0, math_style="inline")]


def test_preview_baseline_maps_directly_to_word_position() -> None:
    """Map every backend's preview depth directly to Word half-points."""
    inline = docx_ole.build_mathtype_template()
    inline.baseline_from_bottom_pt = 3.0
    display = docx_ole.build_mathtype_template()
    display.baseline_from_bottom_pt = 3.0

    assert docx_ole.mathtype_position_half_points(inline) == -6
    assert docx_ole.mathtype_position_half_points(display) == -6

    shallow = docx_ole.build_mathtype_template()
    shallow.baseline_from_bottom_pt = 0.24
    assert docx_ole.mathtype_position_half_points(shallow) == 0


def test_zero_depth_baseline_does_not_use_height_fallback(tmp_path) -> None:
    """Keep an ascender-only glyph on its real zero-depth baseline."""
    metadata_path = tmp_path / "b.json"
    metadata_path.write_text(
        '{"mathtype":{"baseline_from_bottom_pt":0.0},'
        '"renderer":{"baseline_source":"ratex-layout-depth"}}',
        encoding="utf-8",
    )
    equation = ole_parts.GeneratedEquation(
        latex="b",
        ole_path=tmp_path / "b.ole.bin",
        wmf_path=tmp_path / "b.wmf",
        metadata_path=metadata_path,
        math_style="inline",
    )
    template = docx_ole.build_mathtype_template()
    template.baseline_from_bottom_pt = equation.baseline_from_bottom_pt

    assert equation.baseline_from_bottom_pt == 0.0
    assert docx_ole.mathtype_position_half_points(template) == 0


def test_make_wmf_metadata_from_mtef_uses_sdk_xform_ole(monkeypatch, tmp_path) -> None:
    """Use the helper's MTEF SDK path to create Rust-path WMF and JSON files."""
    calls = []
    mtef_path = tmp_path / "eq.mtef.bin"
    helper_ole_path = tmp_path / "eq.sdk.ole.bin"
    wmf_path = tmp_path / "eq.wmf"
    metadata_path = tmp_path / "eq.json"
    prefs_path = tmp_path / "size.eqp"
    helper_exe = tmp_path / "MathTypeOleHelper.exe"

    monkeypatch.setattr(ole_parts, "require_helper_executable", lambda: helper_exe)
    monkeypatch.setattr(ole_parts, "run", lambda command, **kwargs: calls.append(command))

    ole_parts.make_wmf_metadata_from_mtef(
        mtef_path,
        helper_ole_path,
        wmf_path,
        metadata_path,
        prefs_file=prefs_path,
    )

    command = calls[0]
    assert command[command.index("--method") + 1] == "sdk-xform-ole"
    assert command[command.index("--format") + 1] == "MathType EF"
    assert command[command.index("--input") + 1] == str(mtef_path)
    assert command[command.index("--output") + 1] == str(helper_ole_path)
    assert command[command.index("--preview-output") + 1] == str(wmf_path)
    assert command[command.index("--metadata-output") + 1] == str(metadata_path)
    assert command[command.index("--prefs-file") + 1] == str(prefs_path)
    assert "--binary" in command


def test_generate_uncached_equation_parts_uses_rust_sdk_method(monkeypatch, tmp_path) -> None:
    """Keep the former Rust plus MathType SDK pipeline under rust-sdk."""
    calls = []

    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_rust_sdk",
        lambda *args, **kwargs: calls.append("rust-sdk"),
    )

    ole_parts.generate_uncached_equation_parts(
        1,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        conversion_method="rust-sdk",
    )

    assert calls == ["rust-sdk"]


def test_rust_sdk_generates_rust_ole_before_helper_preview(monkeypatch, tmp_path) -> None:
    """Generate authoritative Rust OLE/MTEF before the temporary SDK preview OLE."""
    calls = []
    input_path = tmp_path / "eq.tex"
    ole_path = tmp_path / "eq.ole.bin"
    wmf_path = tmp_path / "eq.wmf"
    metadata_path = tmp_path / "eq.json"
    mtef_path = tmp_path / "eq.mtef.bin"

    def fake_rust(input_file, output_file, mtef_output, prefs_file=None):
        """Write the authoritative Rust artifacts for the ordering probe."""
        calls.append("rust")
        output_file.write_bytes(b"rust-ole")
        mtef_output.write_bytes(b"rust-mtef")

    def fake_sdk(mtef_file, helper_ole, wmf_output, metadata_output, prefs_file=None):
        """Write preview artifacts after confirming the Rust MTEF exists."""
        calls.append("sdk")
        assert mtef_file.read_bytes() == b"rust-mtef"
        helper_ole.write_bytes(b"temporary-sdk-ole")
        wmf_output.write_bytes(b"wmf")
        metadata_output.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(ole_parts, "make_ole_from_mathtype_rust", fake_rust)
    monkeypatch.setattr(ole_parts, "make_wmf_metadata_from_mtef", fake_sdk)

    ole_parts.make_ole_wmf_metadata_with_mathtype_rust_sdk(
        input_path,
        ole_path,
        wmf_path,
        metadata_path,
        mtef_path,
    )

    assert calls == ["rust", "sdk"]
    assert ole_path.read_bytes() == b"rust-ole"
    assert not (tmp_path / "eq.ole.sdk.bin").exists()


def test_generate_uncached_equation_parts_uses_rust_method(monkeypatch, tmp_path) -> None:
    """Use only the Rust path when style metadata selects rust."""
    calls = []

    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_rust",
        lambda *args, **kwargs: calls.append("rust"),
    )
    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_set_data",
        lambda *args, **kwargs: calls.append("set-data"),
    )

    ole_parts.generate_uncached_equation_parts(
        1,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        conversion_method="rust",
    )

    assert calls == ["rust"]


def test_generate_uncached_equation_parts_uses_set_data_method(monkeypatch, tmp_path) -> None:
    """Use only MathType TeX import when style metadata selects set-data."""
    calls = []

    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_rust",
        lambda *args, **kwargs: calls.append("rust"),
    )
    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_set_data",
        lambda *args, **kwargs: calls.append("set-data"),
    )

    ole_parts.generate_uncached_equation_parts(
        1,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        conversion_method="set-data",
    )

    assert calls == ["set-data"]

def test_generate_uncached_equation_parts_auto_uses_rust_when_mathtype_unavailable(
    monkeypatch, tmp_path
) -> None:
    """Skip both COM-backed methods when Windows MathType is unavailable."""
    calls = []

    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        ole_parts,
        "check_mathtype_availability",
        lambda method: ole_parts.MathTypeAvailability(("missing",), ()),
    )
    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_rust",
        lambda *args, **kwargs: calls.append("rust"),
    )

    ole_parts.generate_uncached_equation_parts(
        1,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        conversion_method="auto",
    )

    assert calls == ["rust"]


def test_generate_uncached_equation_parts_auto_uses_rust_on_non_windows(monkeypatch, tmp_path) -> None:
    """Keep auto mode fully cross-platform by using Rust directly off Windows."""
    calls = []

    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        ole_parts,
        "check_mathtype_availability",
        lambda method: pytest.fail("MathType availability must not be probed off Windows"),
    )
    monkeypatch.setattr(
        ole_parts,
        "make_ole_wmf_metadata_with_mathtype_rust",
        lambda *args, **kwargs: calls.append("rust"),
    )

    ole_parts.generate_uncached_equation_parts(
        1,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        conversion_method="auto",
    )

    assert calls == ["rust"]


def test_generate_cached_equation_parts_auto_uses_resolved_order(monkeypatch, tmp_path) -> None:
    """Apply the resolved set-data, rust-sdk, Rust order at the cache boundary."""
    calls = []

    def fake_generate(*args, **kwargs):
        method = args[13]
        calls.append(method)
        if method != "rust":
            raise RuntimeError(f"{method} failed")
        return False

    monkeypatch.setattr(ole_parts, "generate_cached_equation_parts_for_method", fake_generate)

    hits, misses = ole_parts.generate_cached_equation_parts_auto(
        1,
        "x",
        None,
        None,
        None,
        None,
        None,
        tmp_path / "eq.tex",
        tmp_path / "eq.ole.bin",
        tmp_path / "eq.wmf",
        tmp_path / "eq.json",
        tmp_path / "eq.mtef.bin",
        None,
        ("set-data", "rust-sdk", "rust"),
    )

    assert calls == ["set-data", "rust-sdk", "rust"]
    assert (hits, misses) == (0, 3)


def test_normalize_conversion_method_accepts_style_aliases() -> None:
    """Normalize user-facing style metadata values to conversion backends."""
    assert ole_parts.normalize_conversion_method(None) == "auto"
    assert ole_parts.normalize_conversion_method("mathtype-rust") == "rust"
    assert ole_parts.normalize_conversion_method("rust-sdk") == "rust-sdk"
    assert ole_parts.normalize_conversion_method("sdk-xform-ole") == "rust-sdk"
    assert ole_parts.normalize_conversion_method("tex") == "set-data"
    assert ole_parts.normalize_conversion_method("fallback") == "auto"
    assert ole_parts.normalize_conversion_method("both") == "both"


def test_normalize_svg_backend_accepts_documented_values() -> None:
    """Normalize both cross-platform SVG renderer names from style metadata."""
    assert ole_parts.normalize_svg_backend(None) == "typst"
    assert ole_parts.normalize_svg_backend("RaTeX") == "ratex"
    assert ole_parts.normalize_svg_backend("typst-as-lib") == "typst"


def test_generate_equation_parts_both_uses_independent_backend_caches(monkeypatch, tmp_path) -> None:
    """Generate both backends with separate cache methods and keep set-data output."""
    calls = []
    warnings = []
    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ole_parts, "iter_equation_requests_with_progress", lambda requests: enumerate(requests, start=1))
    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: f"mtef:{Path(path).name}")
    monkeypatch.setattr(ole_parts, "native_source_digest_for_method", lambda method: "rust-source")
    monkeypatch.setattr(ole_parts, "restore_cached_equation", lambda *args: False)
    monkeypatch.setattr(ole_parts, "store_cached_equation", lambda *args: None)
    monkeypatch.setattr(ole_parts, "inspect_ole", lambda path: FakeCompound())
    monkeypatch.setattr(ole_parts, "log_warning", lambda message: warnings.append(message))

    def fake_generate_uncached(index, input_path, ole_path, wmf_path, metadata_path, mtef_path, **kwargs):
        method = kwargs["conversion_method"]
        calls.append(method)
        ole_path.write_bytes(b"ole-" + method.encode())
        wmf_path.write_bytes(minimal_wmf())
        metadata_path.write_text('{"method":"' + method + '"}', encoding="utf-8")

    monkeypatch.setattr(ole_parts, "generate_uncached_equation_parts", fake_generate_uncached)

    latex = r"\frac{a}{b} + \alpha"
    equations = ole_parts.generate_equation_parts(
        [ole_parts.EquationRequest(latex)],
        tmp_path,
        conversion_method="both",
    )

    assert calls == ["rust", "set-data"]
    assert equations[0].ole_path == tmp_path / "eq_001.ole.bin"
    assert (tmp_path / "eq_001.ole.bin").read_bytes() == b"ole-set-data"
    assert (tmp_path / "eq_001.rust.ole.bin").read_bytes() == b"ole-rust"
    assert any("rust and set-data outputs differ" in message for message in warnings)
    assert any(f"LaTeX: {latex}" in message for message in warnings)


def test_generate_equation_parts_both_rejects_non_windows(monkeypatch, tmp_path) -> None:
    """Reject the Windows-only comparison mode at the conversion boundary."""
    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Linux")

    with pytest.raises(ValueError, match="only supported on Windows"):
        ole_parts.generate_equation_parts([], tmp_path, conversion_method="both")


@pytest.mark.parametrize("failure", ["exception", "invalid-preview", "invalid-metadata", "both-fail"])
def test_both_uses_rust_when_set_data_fails(monkeypatch, tmp_path, failure) -> None:
    """Fallback must replace partial COM output and preserve later formula alignment."""
    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ole_parts, "native_source_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts, "native_library_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts, "inspect_ole", lambda path: FakeCompound())
    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: "same")
    monkeypatch.setattr(ole_parts, "restore_cached_equation", lambda *args: False)
    cached = []
    monkeypatch.setattr(ole_parts, "store_cached_equation", lambda key, ole, *args: cached.append(ole.read_bytes()))

    def generate(index, input_path, ole_path, wmf_path, metadata_path, mtef_path, **kwargs):
        """Emulate failures on formula one while leaving formula two usable."""
        method = kwargs["conversion_method"]
        ole_path.write_bytes(f"{method}:{index}".encode())
        wmf_path.write_bytes(minimal_wmf())
        metadata_path.write_text('{"method":"' + method + '"}', encoding="utf-8")
        if index == 1 and (method == "set-data" or failure == "both-fail"):
            if failure == "invalid-preview":
                wmf_path.write_bytes(b"partial")
            elif failure == "invalid-metadata":
                metadata_path.write_text("{", encoding="utf-8")
            else:
                raise RuntimeError("backend failed after partial output")

    monkeypatch.setattr(ole_parts, "generate_uncached_equation_parts", generate)
    equations = ole_parts.generate_equation_parts(
        [ole_parts.EquationRequest("a"), ole_parts.EquationRequest("b")], tmp_path, "both",
    )
    if failure == "both-fail":
        assert equations[0] is None
        assert b"rust:1" not in cached
    else:
        assert equations[0].ole_path.read_bytes() == b"rust:1"
        assert equations[0].wmf_path.read_bytes() == minimal_wmf()
        assert '"rust"' in equations[0].metadata_path.read_text(encoding="utf-8")
    assert equations[1].ole_path.read_bytes() == b"set-data:2"
    assert b"set-data:1" not in cached


def test_both_disables_set_data_after_three_marked_failures(monkeypatch, tmp_path) -> None:
    """Disable only the unstable set-data backend after three matching failures."""
    calls = []
    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ole_parts, "native_source_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts, "native_library_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts, "restore_cached_equation", lambda *args: False)
    monkeypatch.setattr(ole_parts, "store_cached_equation", lambda *args: None)
    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: "same")
    monkeypatch.setattr(ole_parts, "inspect_ole", lambda path: FakeCompound())

    def generate(index, input_path, ole_path, wmf_path, metadata_path, mtef_path, **kwargs):
        """Fail the first three set-data conversions with the helper fallback text."""
        method = kwargs["conversion_method"]
        calls.append((index, method))
        ole_path.write_bytes(f"{method}:{index}".encode())
        wmf_path.write_bytes(minimal_wmf())
        metadata_path.write_text("{}", encoding="utf-8")
        if method == "set-data" and index <= 3:
            raise RuntimeError(ole_parts.SET_DATA_FAILURE_MARKER)

    monkeypatch.setattr(ole_parts, "generate_uncached_equation_parts", generate)
    equations = ole_parts.generate_equation_parts(
        [ole_parts.EquationRequest(str(index)) for index in range(1, 5)], tmp_path, "both",
    )

    assert [method for _, method in calls] == ["rust", "set-data", "rust", "set-data", "rust", "set-data", "rust"]
    assert [equation.ole_path.read_bytes() for equation in equations] == [b"rust:1", b"rust:2", b"rust:3", b"rust:4"]


def test_exception_marker_is_detected_from_exception_chain() -> None:
    """Recognize the helper fallback text even when wrapped by another exception."""
    try:
        try:
            raise RuntimeError(ole_parts.SET_DATA_FAILURE_MARKER)
        except RuntimeError as exc:
            raise ValueError("wrapped helper failure") from exc
    except ValueError as exc:
        assert ole_parts.exception_contains_set_data_marker(exc)


def test_both_availability_accepts_rust_without_com(monkeypatch) -> None:
    """Do not skip DOCX conversion merely because MathType is absent."""
    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ole_parts, "check_cross_platform_mathtype_availability", lambda: ole_parts.MathTypeAvailability((), ()))
    monkeypatch.setattr(ole_parts, "_read_hkcr_default", lambda key: pytest.fail("COM must not block Rust"))
    assert ole_parts.check_mathtype_availability("both").usable


def test_warn_if_conversion_outputs_differ_accepts_equal_mtef(monkeypatch, tmp_path) -> None:
    """Equal formula data must not warn or require preview metadata."""
    warnings = []
    rust_ole = tmp_path / "rust.ole.bin"
    set_data_ole = tmp_path / "set-data.ole.bin"
    for path in (rust_ole, set_data_ole):
        path.write_bytes(b"not-used")

    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: "same-mtef")
    monkeypatch.setattr(ole_parts, "log_warning", lambda message: warnings.append(message))

    ole_parts.warn_if_conversion_outputs_differ(
        1,
        "x",
        rust_ole,
        set_data_ole,
    )

    assert warnings == []


def test_warn_if_conversion_outputs_differ_reports_ole_mtef(monkeypatch, tmp_path) -> None:
    """Keep formula-data differences visible regardless of preview metadata."""
    warnings = []
    rust_ole = tmp_path / "rust.ole.bin"
    set_data_ole = tmp_path / "set-data.ole.bin"
    for path in (rust_ole, set_data_ole):
        path.write_bytes(b"not-used")

    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: Path(path).stem)
    monkeypatch.setattr(ole_parts, "log_warning", lambda message: warnings.append(message))

    ole_parts.warn_if_conversion_outputs_differ(
        2,
        "x",
        rust_ole,
        set_data_ole,
    )

    assert len(warnings) == 1
    assert "OLE MTEF" in warnings[0]
    assert "JSON" not in warnings[0]
    assert "WMF" not in warnings[0]


def test_convert_marked_docx_passes_style_conversion_method(monkeypatch, tmp_path) -> None:
    """Pass conversion and SVG backend selections into MathType part generation."""
    seen = {}

    monkeypatch.setattr(
        convert_marked_docx_module,
        "extract_marked_equation_requests",
        lambda source: [ole_parts.EquationRequest("x")],
    )
    monkeypatch.setattr(
        convert_marked_docx_module,
        "replace_marked_omml_with_generated",
        lambda source, target, equations: 1,
    )
    monkeypatch.setattr(convert_marked_docx_module, "inspect_docx", lambda target: None)

    def fake_generate_equation_parts(
        requests,
        output_dir,
        conversion_method="rust",
        svg_backend="ratex",
        math_font="XITS Math",
    ):
        seen["conversion_method"] = conversion_method
        seen["svg_backend"] = svg_backend
        return [
            ole_parts.GeneratedEquation(
                latex=requests[0].latex,
                ole_path=tmp_path / "eq.ole.bin",
                wmf_path=tmp_path / "eq.wmf",
            )
        ]

    monkeypatch.setattr(convert_marked_docx_module, "generate_equation_parts", fake_generate_equation_parts)

    replaced = convert_marked_docx_module.convert_marked_docx(
        tmp_path / "source.docx",
        tmp_path / "target.docx",
        tmp_path / "work",
        pmt_settings=PmtSettings.model_validate(
            {
                "mathtypeConversionMethod": "set-data",
                "mathtypeSvgBackend": "typst",
            }
        ),
    )

    assert replaced == 1
    assert seen["conversion_method"] == "set-data"
    assert seen["svg_backend"] == "typst"


def test_mathtype_cache_key_includes_rust_converter_and_method_digests() -> None:
    """Invalidate cache entries when the Rust converter or selected method changes."""
    base = ole_parts.mathtype_cache_key("x", None, None, "helper", "rust-src-a", "rust-exe", "rust")
    changed_helper = ole_parts.mathtype_cache_key("x", None, None, "changed-helper", "rust-src-a", "rust-exe", "rust")
    changed_source = ole_parts.mathtype_cache_key("x", None, None, "helper", "rust-src-b", "rust-exe", "rust")
    changed_exe = ole_parts.mathtype_cache_key("x", None, None, "helper", "rust-src-a", "changed-rust-exe", "rust")
    changed_method = ole_parts.mathtype_cache_key("x", None, None, "helper", "rust-src-a", "rust-exe", "set-data")
    changed_svg_backend = ole_parts.mathtype_cache_key(
            "x", None, None, "helper", "rust-src-a", "rust-exe", "rust", "ratex"
    )
    changed_math_style = ole_parts.mathtype_cache_key(
        "x", None, None, "helper", "rust-src-a", "rust-exe", "rust", "ratex", "inline"
    )
    set_data_base = ole_parts.mathtype_cache_key("x", None, None, "helper", "rust-src-a", "rust-exe", "set-data")
    set_data_changed_rust = ole_parts.mathtype_cache_key(
        "x",
        None,
        None,
        "helper",
        "rust-src-b",
        "changed-rust-exe",
        "set-data",
    )
    set_data_inline = ole_parts.mathtype_cache_key(
        "x", None, None, "helper", "rust-src-a", "rust-exe", "set-data", "ratex", "inline"
    )

    rust_sdk_base = ole_parts.mathtype_cache_key(
        "x", None, None, "helper", "rust-src-a", "rust-exe", "rust-sdk"
    )
    rust_sdk_changed_helper = ole_parts.mathtype_cache_key(
        "x", None, None, "changed-helper", "rust-src-a", "rust-exe", "rust-sdk"
    )
    assert base != changed_source
    assert base != changed_exe
    assert base != changed_method
    assert base != changed_svg_backend
    assert base != changed_math_style
    assert base == changed_helper
    assert rust_sdk_base != rust_sdk_changed_helper
    assert set_data_base == set_data_changed_rust
    assert set_data_base == set_data_inline


class FakeCompound:
    """Minimal OLE inspector double with a DSMT-bearing native stream."""

    def read_stream(self, name: str) -> bytes:
        """Return a valid MathType marker for generated-output validation."""
        return b"DSMT"


@pytest.mark.parametrize("method,failed_stage", [
    ("rust", "wmf"), ("auto", "wmf"), ("both", "wmf"),
    ("rust", "ole"), ("auto", "ole"), ("both", "ole"), ("rust-sdk", "ole"),
])
@pytest.mark.parametrize("failed_indices", [{2}, {1, 2, 3}])
def test_failed_native_conversion_continues_docx_build(monkeypatch, tmp_path, method, failed_stage, failed_indices) -> None:
    """Preserve failed OMML, later formula alignment, and usable both-mode output."""
    warnings = []
    visited = []
    work = tmp_path / "work"
    cache = tmp_path / "cache"
    source = tmp_path / "source.docx"
    target = tmp_path / "result.docx"
    formulas = ["a", r"\unsupported{b}", "c"]
    paragraphs = "".join(
        f'<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>MTLATEX:inline:{latex}</w:t></w:r>'
        f'<m:oMath><m:r><m:t>{latex}</m:t></m:r></m:oMath></w:p>'
        for latex in formulas
    )
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{marked_docx.NS["w"]}" xmlns:m="{marked_docx.NS["m"]}">'
            f'<w:body>{paragraphs}</w:body></w:document>',
        )
        archive.writestr("word/styles.xml", f'<w:styles xmlns:w="{marked_docx.NS["w"]}"/>')
        archive.writestr("word/_rels/document.xml.rels", f'<Relationships xmlns="{marked_docx.NS["rel"]}"/>')
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')

    class FakeConverter:
        """Return native artifacts or report the selected formula's conversion failure."""

        def __init__(self, project):
            """Select OLE encoding or WMF rendering for this library double."""
            self.project = project

        def call(self, **request):
            """Fail by formula identity so document alignment is independently checked."""
            latex = request["latex"]
            index = [ole_parts.mathtype_tex_payload(value) for value in formulas].index(latex) + 1
            stage = "wmf" if request.get("operation") == "render_wmf" else "ole"
            if stage == "ole":
                visited.append(index)
            if stage == failed_stage and index in failed_indices:
                raise RuntimeError("unsupported formula")
            if stage == "ole":
                return {"ole": latex.encode(), "mtef": b"mtef"}
            return {"wmf": minimal_wmf(), "metadata_json": "{}"}

    def fake_set_data(input_path, ole_path, wmf_path, metadata_path, **kwargs):
        """Provide native output independently of the failed Rust renderer."""
        ole_path.write_bytes(input_path.read_bytes())
        wmf_path.write_bytes(minimal_wmf())
        metadata_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(ole_parts.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ole_parts, "MATHTYPE_CACHE_DIR", cache)
    monkeypatch.setattr(ole_parts, "resolve_auto_conversion_methods", lambda: ("rust",))
    monkeypatch.setattr(ole_parts, "native_source_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts.native, "get_converter", FakeConverter)
    monkeypatch.setattr(ole_parts, "native_library_digest_for_method", lambda method: None)
    monkeypatch.setattr(ole_parts, "make_ole_wmf_metadata_with_mathtype_set_data", fake_set_data)
    monkeypatch.setattr(ole_parts, "make_wmf_metadata_from_mtef", fake_set_data)
    monkeypatch.setattr(ole_parts, "inspect_ole", lambda path: FakeCompound())
    monkeypatch.setattr(ole_parts, "mathtype_ole_mtef_sha256", lambda path: path.read_bytes())
    monkeypatch.setattr(ole_parts, "log_warning", warnings.append)

    replaced = convert_marked_docx_module.convert_marked_docx(
        source, target, work,
        PmtSettings.model_validate({"mathtypeConversionMethod": method}),
    )
    expected = 3 if method == "both" else 3 - len(failed_indices)
    assert replaced == expected
    assert visited == [1, 2, 3]
    for index in failed_indices:
        assert any("unsupported formula" in message and formulas[index - 1] in message for message in warnings)
        stem = f"eq_{index:03d}" + (".rust" if method == "both" else "")
        assert not (work / f"{stem}.wmf").exists()
        assert not (work / f"{stem}.json").exists()
        if failed_stage == "ole":
            assert not (work / f"{stem}.ole.bin").exists()
            assert not (work / f"{stem}.mtef.bin").exists()
    assert len(list(cache.rglob("preview.wmf"))) == (6 - len(failed_indices) if method == "both" else expected)
    with zipfile.ZipFile(target) as archive:
        document = marked_docx.read_xml(archive, "word/document.xml")
        assert b"MTLATEX:" not in archive.read("word/document.xml")
        for index, paragraph in enumerate(document.findall(".//w:body/w:p", marked_docx.NS), start=1):
            if method != "both" and index in failed_indices:
                assert paragraph.find(".//m:t", marked_docx.NS).text == formulas[index - 1]
                assert paragraph.find(".//w:object", marked_docx.NS) is None
            else:
                assert paragraph.find(".//m:oMath", marked_docx.NS) is None
                assert paragraph.find(".//w:object", marked_docx.NS) is not None
                assert archive.read(f"word/embeddings/mathtype_formula_{index}.bin") == ole_parts.mathtype_tex_payload(formulas[index - 1]).encode()
