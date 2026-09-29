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
from pandoc_manuscript.runtime import paths as runtime_paths
from pandoc_manuscript.runtime.metadata import PmtSettings
from pandoc_manuscript.runtime.paths import (
    process_temp_dir,
    project_cache_dir,
    project_state_dir,
)


def test_generated_work_and_cache_paths_are_isolated(tmp_path, monkeypatch) -> None:
    """Separate reusable project caches from large per-run intermediates."""
    monkeypatch.chdir(tmp_path)
    state = project_state_dir()
    assert state.parent == Path.home() / ".papper" / "projects"
    assert reply_build.line_source_cache_dir().is_relative_to(state / "cache")
    assert ole_parts.mathtype_cache_dir().is_relative_to(state / "cache")
    assert reply_build.line_source_pdf_dir().is_relative_to(process_temp_dir())
    assert reply_build.reply_probe_dir().is_relative_to(process_temp_dir())


def test_cache_resolution_follows_project_changes(tmp_path, monkeypatch) -> None:
    """Switch projects in one process without reusing the first project's cache."""
    monkeypatch.setattr(runtime_paths, "PAPPER_HOME_DIR", tmp_path / "user-state")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    first_paths = (ole_parts.cache_paths("abcdef"), reply_build.line_source_cache_dir())
    monkeypatch.chdir(second)
    second_paths = (ole_parts.cache_paths("abcdef"), reply_build.line_source_cache_dir())

    assert first_paths != second_paths
    assert second_paths[0][0].is_relative_to(project_cache_dir(second))
    assert second_paths[1].is_relative_to(project_cache_dir(second))


def test_build_resource_path_is_forwarded_without_normalization(tmp_path, monkeypatch) -> None:
    """Forward an explicit resource path string to Pandoc exactly as entered."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("Body\n", encoding="utf-8")
    captured: list[list[str]] = []
    monkeypatch.setattr(build, "run_command", lambda command, **_: captured.append(command))
    monkeypatch.setattr(build, "pandoc_command", lambda: "pandoc")
    monkeypatch.setattr(build, "pandoc_tools_env", lambda env=None: env or {})
    monkeypatch.setattr(build, "write_markdown_without_yaml_header", lambda _: None)
    monkeypatch.setattr(build, "style_metadata_args", lambda _: [])
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", str(manuscript))
    monkeypatch.setattr(build.SETTINGS, "resource_path", r"C:\raw;../with spaces")

    build.run_pandoc(
        Path("defaults.yml"),
        tmp_path / "out.docx",
        build.EffectiveMetadata(
            pmt_settings=build.PmtSettings.model_validate({}),
            pandoc_metadata={},
            has_yaml_header=False,
        ),
    )

    command = captured[0]
    index = command.index("--resource-path")
    assert command[index + 1] == r"C:\raw;../with spaces"


def test_svg_to_png_cache_uses_pmt_cache(monkeypatch) -> None:
    """Route SVG rasterization artifacts away from final output directories."""
    monkeypatch.setattr(build.SETTINGS, "manuscript_file", "manuscript.md")

    env = docx_build.docx_svg_to_png_filter_env(
        build.SETTINGS.manuscript_file, PmtSettings.model_validate({})
    )

    assert Path(env["PMT_SVG_TO_PNG_DIR"]) == project_cache_dir() / "svg-png"
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

    assert Path(env["PMT_SVG_EMBED_DIR"]) == project_cache_dir() / "svg-embedded"
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

    assert Path(env["PMT_SVG_EMBED_DIR"]) == project_cache_dir() / "svg-embedded"
    assert env["PMT_SVG_EMBED_IMAGES"] == "true"
    assert str(tmp_path.resolve()) in env["PMT_SVG_EMBED_BASE_DIRS"]


def test_reply_svg_to_png_env_uses_shared_cache(tmp_path) -> None:
    """Route reply SVG rasterization cache files through the shared papper cache."""
    reply = tmp_path / "reply.md"

    env = reply_build.svg_to_png_filter_env(
        reply,
        PmtSettings.model_validate({"docxConvertSvgToPng": True}),
    )

    assert Path(env["PMT_SVG_TO_PNG_DIR"]) == project_cache_dir() / "svg-png"
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
        assert wrapper.is_relative_to(process_temp_dir())


def test_reply_line_source_cache_uses_pmt_cache() -> None:
    """Keep reusable reply line-source artifacts under the shared papper cache."""
    assert reply_build.line_source_cache_dir() == project_cache_dir() / "reply" / "line-source"


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
