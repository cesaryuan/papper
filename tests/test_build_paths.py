from pathlib import Path
import os
import shutil
import subprocess
import sys
from typing import get_args

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import build, build_reply as reply_build
from pandoc_manuscript.docx import build as docx_build
from pandoc_manuscript.docx.svg_filters import python_filter_wrapper
from pandoc_manuscript.mathtype import ole_parts
from pandoc_manuscript.runtime import resources
from pandoc_manuscript.runtime.metadata import PmtSettings
from pandoc_manuscript.runtime.paths import (
    PMT_CACHE_DIR,
    PMT_DIR,
    PMT_REPLY_LINE_SOURCE_CACHE_DIR,
    PMT_SVG_EMBED_CACHE_DIR,
    PMT_SVG_PNG_CACHE_DIR,
    PMT_WORK_DIR,
)


def test_generated_work_and_cache_paths_are_under_pmt() -> None:
    """Keep Papper's temporary files and reusable caches in one hidden project directory."""
    paths = [
        Path(build.SETTINGS.mathtype_work_dir),
        reply_build.LINE_SOURCE_PDF_DIR,
        reply_build.LINE_SOURCE_CACHE_DIR,
        reply_build.REPLY_PROBE_DIR,
        ole_parts.MATHTYPE_CACHE_DIR,
    ]

    for path in paths:
        assert path.parts[0] == PMT_DIR.name


def test_svg_to_png_cache_uses_pmt_cache(monkeypatch) -> None:
    """Route SVG rasterization artifacts away from final output directories."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_to_png_filter_env(
        build.SETTINGS.manuscript_file, PmtSettings.model_validate({})
    )

    assert Path(env["PMT_SVG_TO_PNG_DIR"]) == (Path.cwd() / PMT_CACHE_DIR / "svg-png").resolve()
    assert env["PMT_SVG_TO_PNG_CONVERT_ALL"] == "false"


def test_citation_range_delimiter_uses_filter_environment() -> None:
    """Pass the Papper-owned citation delimiter without adding Pandoc metadata."""
    settings = PmtSettings.model_validate({"citationNumberRangeDelimiter": "-"})

    env = build.pandoc_filter_env(settings)

    assert env == {"PMT_CITATION_NUMBER_RANGE_DELIMITER": "-"}


def test_default_citation_range_delimiter_skips_filter_environment() -> None:
    """Let citeproc retain its native en dash without a Unicode environment value."""
    settings = PmtSettings.model_validate({"citationNumberRangeDelimiter": "–"})

    assert build.pandoc_filter_env(settings) == {}


def test_default_citation_range_delimiter_survives_pandoc_lua_filter(tmp_path: Path) -> None:
    """Keep collapsed citation ranges intact on Windows Lua environment reads."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    bibliography = tmp_path / "references.bib"
    bibliography.write_text(
        "@article{one, author = {One, Ada}, title = {One}, journal = {Journal}, year = {2020}}\n"
        "@article{two, author = {Two, Bea}, title = {Two}, journal = {Journal}, year = {2021}}\n"
        "@article{three, author = {Three, Cy}, title = {Three}, journal = {Journal}, year = {2022}}\n",
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update(
        build.pandoc_filter_env(
            PmtSettings.model_validate({"citationNumberRangeDelimiter": "–"})
        )
    )

    result = subprocess.run(
        [
            pandoc,
            "--citeproc",
            "--bibliography",
            str(bibliography),
            "--csl",
            str(repo_root / "pandoc" / "csl" / "elsevier-vancouver.csl"),
            "--lua-filter",
            str(repo_root / "pandoc" / "filters" / "docx" / "citation_number_range_delimiter.lua"),
            "--from",
            "markdown",
            "--to",
            "plain",
            "--wrap=none",
        ],
        input="References [@one; @two; @three].\n",
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=environment,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "References [1–3]." in result.stdout
    assert "\ufffd" not in result.stdout


def test_svg_embed_cache_uses_pmt_cache(monkeypatch) -> None:
    """Route self-contained SVG cache files away from final output directories."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_embed_images_filter_env(
        build.SETTINGS.manuscript_file, PmtSettings.model_validate({})
    )

    assert Path(env["PMT_SVG_EMBED_DIR"]) == (Path.cwd() / PMT_SVG_EMBED_CACHE_DIR).resolve()
    assert env["PMT_SVG_EMBED_IMAGES"] == "true"


def test_svg_embed_env_keeps_global_embedding_switch(monkeypatch) -> None:
    """Pass the global SVG child-image embedding switch to the DOCX filter."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_embed_images_filter_env(
        build.SETTINGS.manuscript_file,
        PmtSettings.model_validate({"docxEmbedSvgImages": True})
    )

    assert env["PMT_SVG_EMBED_IMAGES"] == "true"


def test_svg_embed_env_disables_embedding_when_global_png_conversion_is_enabled(monkeypatch) -> None:
    """Keep full SVG rasterization from doing redundant child-image embedding first."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_embed_images_filter_env(
        build.SETTINGS.manuscript_file,
        PmtSettings.model_validate(
            {"docxEmbedSvgImages": True, "docxConvertSvgToPng": True}
        )
    )

    assert env["PMT_SVG_EMBED_IMAGES"] == "false"


def test_reply_svg_embed_env_uses_shared_cache(tmp_path) -> None:
    """Route reply self-contained SVG cache files through the shared papper cache."""
    reply = tmp_path / "reply.md"

    env = reply_build.svg_embed_images_filter_env(
        reply,
        PmtSettings.model_validate({"docxEmbedSvgImages": True}),
    )

    assert Path(env["PMT_SVG_EMBED_DIR"]) == (Path.cwd() / PMT_SVG_EMBED_CACHE_DIR).resolve()
    assert env["PMT_SVG_EMBED_IMAGES"] == "true"
    assert str(tmp_path.resolve()) in env["PMT_SVG_EMBED_BASE_DIRS"]


def test_reply_svg_to_png_env_uses_shared_cache(tmp_path) -> None:
    """Route reply SVG rasterization cache files through the shared papper cache."""
    reply = tmp_path / "reply.md"

    env = reply_build.svg_to_png_filter_env(
        reply,
        PmtSettings.model_validate({"docxConvertSvgToPng": True}),
    )

    assert Path(env["PMT_SVG_TO_PNG_DIR"]) == (Path.cwd() / PMT_SVG_PNG_CACHE_DIR).resolve()
    assert env["PMT_SVG_TO_PNG_CONVERT_ALL"] == "true"
    assert str(tmp_path.resolve()) in env["PMT_SVG_TO_PNG_BASE_DIRS"]


def test_svg_to_png_env_keeps_global_conversion_switch(monkeypatch) -> None:
    """Pass the global SVG rasterization switch to the shared DOCX filter."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_to_png_filter_env(
        build.SETTINGS.manuscript_file,
        PmtSettings.model_validate({"docxConvertSvgToPng": True})
    )

    assert env["PMT_SVG_TO_PNG_CONVERT_ALL"] == "true"


def test_svg_to_png_env_passes_width_control(monkeypatch) -> None:
    """Pass the optional SVG-to-PNG output width to the shared DOCX filter."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_to_png_filter_env(
        build.SETTINGS.manuscript_file,
        PmtSettings.model_validate({"docxSvgToPngWidth": 1600})
    )

    assert env["PMT_SVG_TO_PNG_WIDTH"] == "1600"


@pytest.mark.parametrize(
    "metadata",
    [
        {"docxSvgToPngWidth": 1600, "docxSvgToPngScale": 2},
        {"docxSvgToPngWidth": 1600, "docxSvgToPngDpi": 300},
        {"docxSvgToPngScale": 2, "docxSvgToPngDpi": 300},
    ],
)
def test_svg_to_png_size_controls_are_mutually_exclusive(monkeypatch, metadata: dict[str, object]) -> None:
    """Reject ambiguous global SVG-to-PNG size controls."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    with pytest.raises(ValueError, match="Only one of docxSvgToPngWidth"):
        PmtSettings.model_validate(metadata)


def test_python_filter_launcher_path(monkeypatch, tmp_path) -> None:
    """Use the source filter directly on Windows and a generated launcher elsewhere."""
    filter_path = tmp_path / "filter.py"
    filter_path.write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    wrapper = python_filter_wrapper(filter_path, "sample_filter")

    if os.name == "nt":
        assert wrapper == filter_path.resolve()
    else:
        assert wrapper.parts[: len(PMT_WORK_DIR.parts)] == PMT_WORK_DIR.parts


def test_reply_line_source_cache_uses_pmt_cache() -> None:
    """Keep reusable reply line-source artifacts under the shared papper cache."""
    assert reply_build.LINE_SOURCE_CACHE_DIR == PMT_REPLY_LINE_SOURCE_CACHE_DIR
    assert reply_build.LINE_SOURCE_CACHE_DIR.parts[: len(PMT_CACHE_DIR.parts)] == PMT_CACHE_DIR.parts


def test_runtime_resources_resolve_source_checkout_roots() -> None:
    """Find repo, template, and Pandoc runtime roots after moving project helpers."""
    repo_root = Path(__file__).resolve().parents[1]

    assert resources.source_tree_root() == repo_root
    assert resources.template_root() == repo_root
    assert resources.project_template_root() == repo_root / "template"


def test_mathtype_helper_paths_live_under_mathtype_package() -> None:
    """Resolve only the prebuilt MathType OLE helper at conversion time."""
    assert "mathtype" in ole_parts.HELPER_EXE.parts
    assert ole_parts.require_helper_executable() == ole_parts.HELPER_EXE


def test_build_target_excludes_clean_commands() -> None:
    """Keep clean and distclean outside the manuscript build target union."""
    assert set(get_args(build.BuildTarget)) == {"docx", "latex", "html", "json"}
