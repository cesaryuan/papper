"""
Build manuscript targets through Papper's Pandoc workflow.
"""

import errno
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, Tuple

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, CliPositionalArg, CliSuppress, PydanticBaseSettingsSource, SettingsConfigDict

from ..runtime.logging import log_error, log_info, log_success, log_warning, log_debug
from ..mathtype.preflight import warn_mathtype_hat_style_order
from ..runtime.metadata import (
    EffectiveMetadata,
    PmtSettings,
    load_effective_metadata,
    merge_metadata,
    write_markdown_without_lang,
    write_pandoc_metadata,
)
from ..mathtype.convert_marked_docx import convert_marked_docx
from ..mathtype.ole_parts import check_mathtype_availability, normalize_conversion_method
from ..runtime.paths import (
    PMT_MATHTYPE_WORK_DIR,
    PMT_WORK_DIR,
    pmt_path,
)
from ..docx.equation_layout import derive_docx_equation_layout, sync_eqn_block_template_with_page_margins
from ..docx.page_margins import write_reference_doc_with_page_margins
from ..docx.postprocess.final_docx_syntax_check import validate_final_docx_syntax
from ..docx.postprocess import postprocess_docx as run_docx_postprocess
from ..runtime.resources import package_resource_path, template_root
from ..docx.svg_filters import (
    python_filter_wrapper,
    should_convert_docx_svg_to_png,
    should_embed_docx_svg_images,
)
from ..docx import svg_filters as svg_filter_helpers
from ..html.build import build_html
from .pandoc_server import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from .setup import ensure_pandoc_tools, pandoc_command, pandoc_tools_env
from .common import VerboseCommandSettings, project_directory, run_streaming_command

# ============================================================================
# SETTINGS
# ============================================================================


DEFAULT_OUTPUT_DIR = "output"
DEFAULT_DOCX_CSL = "pandoc/csl/elsevier-vancouver.csl"
DEFAULT_CHINESE_DOCX_CSL = "pandoc/csl/GB-T-7714—2015（顺序编码，双语，姓名不大写，无URL、DOI）.csl"
PMT_CITATION_NUMBER_RANGE_DELIMITER_ENV = "PMT_CITATION_NUMBER_RANGE_DELIMITER"
BuildTarget = Literal["docx", "latex", "html", "json"]
BUILD_CLI_CONFIG = SettingsConfigDict(
    cli_kebab_case=True,
    cli_implicit_flags=True,
    cli_hide_none_type=True,
    cli_parse_none_str="auto",
    cli_shortcuts={
        "manuscript_option": ["-m", "--manuscript"],
        "output_file": ["-o", "--output-file"],
    },
)


class BuildSettings(BaseSettings):
    """Typed runtime settings shared by CLI parsing and build functions."""

    model_config = SettingsConfigDict(env_prefix="PMT_", extra="ignore")

    project_name: str = "manuscript"
    manuscript_file: str = "manuscript.md"
    style_file: str = "style.yml"
    output_dir: str = DEFAULT_OUTPUT_DIR
    docx_dir: str = "output/docx"
    latex_dir: str = "output/latex"
    html_dir: str = "output/html"
    json_dir: str = "output/json"
    output_file: str | None = None
    enable_docx_postprocess: bool = True
    mathtype_work_dir: str = pmt_path(PMT_MATHTYPE_WORK_DIR)
    reference_doc: str | None = None


SETTINGS = BuildSettings()


class BuildCommandSettings(VerboseCommandSettings):
    """Settings for `papper build`."""

    model_config = BUILD_CLI_CONFIG

    target: CliPositionalArg[BuildTarget] = Field(default="docx", description="Build target.")
    markdown: CliPositionalArg[str | None] = Field(
        default=None,
        description="Input markdown file. Auto: manuscript.md.",
    )
    manuscript_option: str | None = Field(
        default=None,
        validation_alias=AliasChoices("m", "manuscript"),
        description="Input markdown file, equivalent to the positional MARKDOWN argument.",
    )
    output_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("o", "output-file"),
        description="Exact output file path for DOCX, LaTeX, HTML, and JSON builds.",
    )
    mathtype: bool | None = Field(
        default=None,
        description=PmtSettings.model_fields["mathtype"].description,
    )
    lang: str | None = Field(
        default=None,
        description="One-build language mode for DOCX (zh-cn or zhcn).",
    )
    project_dir: Path = Field(default=Path("."), description="Manuscript project directory.")
    reference_doc: CliSuppress[str | None] = Field(
        default=None,
        description="Override the bundled DOCX reference document.",
    )
    start_server: bool = Field(
        default=False,
        description="Start or reuse a local Pandoc HTTP server before an HTML build.",
    )
    server_host: str = Field(default=DEFAULT_SERVER_HOST, description="Local Pandoc server bind host.")
    server_port: int = Field(default=DEFAULT_SERVER_PORT, description="Local Pandoc server HTTP port.")
    server_command: str | None = Field(
        default=None,
        description="Pandoc server command; defaults to PMT_PANDOC_SERVER_COMMAND or PATH lookup.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use parsed CLI values only, so ambient LANG cannot override manuscript metadata."""
        return (init_settings,)

    def run(self) -> int:
        """Run the selected build target."""
        project_dir = self.project_dir.resolve()
        if not project_dir.exists():
            raise FileNotFoundError(f"Project directory not found: {project_dir}")

        with project_directory(project_dir):
            return int(
                run_build_command(
                    target=self.target,
                    markdown=self.markdown,
                    manuscript_option=self.manuscript_option,
                    output_file=self.output_file,
                    reference_doc=self.reference_doc,
                    mathtype=self.mathtype,
                    lang=self.lang,
                    start_server=self.start_server,
                    server_host=self.server_host,
                    server_port=self.server_port,
                    server_command=self.server_command,
                )
            )

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def run_command(
    cmd: list,
    cwd: Path | None = None,
    check: bool = True,
    stream_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a shell command and return the result.

    Args:
        cmd: Command and arguments as a list
        cwd: Working directory for the command
        check: Raise exception on non-zero exit code
        stream_output: If True, stream stdout/stderr to console in real-time
        env: Extra environment variables for this command
    """
    log_debug(f"[Run] {' '.join(str(c) for c in cmd)}")
    command_env = None if env is None else {**os.environ, **env}

    if stream_output:
        return run_streaming_command(cmd, cwd=cwd, check=check, env=command_env)
    else:
        # Capture output (for commands where we need to parse it)
        return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True, env=command_env)


def get_pandoc_version() -> Tuple[int, ...]:
    """Get Pandoc version as a tuple of integers."""
    try:
        pandoc = ensure_pandoc_tools()[0].executable
        result = subprocess.run([str(pandoc), '--version'], capture_output=True, text=True, check=True)
        version_line = result.stdout.split('\n')[0]
        version_str = version_line.split()[1]
        # Handle versions like "3.8.3" or "3.8.3.0"
        parts = version_str.split('.')
        # Pad with zeros if needed (e.g., "3.8.3" -> [3, 8, 3, 0])
        while len(parts) < 4:
            parts.append('0')
        return tuple(int(p) for p in parts[:4])
    except Exception as e:
        log_warning(f"[WARN] Could not detect Pandoc version: {e}")
        return (999, 0, 0, 0)  # Assume latest version


def should_use_mathbfit_filter() -> bool:
    """Check if we should use the mathbfit filter (Pandoc <= 3.8.3.0)."""
    version = get_pandoc_version()
    threshold = (3, 8, 3, 0)
    return version <= threshold


def to_pandoc_path(path: Path) -> str:
    """Return a Pandoc-friendly path string for generated defaults files."""
    return path.as_posix()


def resource_path(path: str | Path) -> Path:
    """Resolve a bundled papper template or package runtime resource path."""
    path = Path(path)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "mathtype":
        return package_resource_path(path)
    return template_root() / path


def configure_manuscript(markdown_path: str | Path, derive_project_name: bool = False) -> None:
    """Configure the runtime markdown input and validate that it exists.

    derive_project_name is used for command-line markdown overrides so a custom
    input such as paper.md writes paper.docx/paper.tex instead of manuscript.*.
    """
    path = Path(markdown_path)
    if not path.exists():
        raise FileNotFoundError(f"Markdown file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Markdown path is not a file: {path}")

    SETTINGS.manuscript_file = to_pandoc_path(path)
    if derive_project_name:
        SETTINGS.project_name = path.stem


def configure_output_dir(output_dir: str | Path) -> None:
    """Configure the base output directory and derived build target directories."""
    path = Path(output_dir)
    if path.exists() and not path.is_dir():
        raise ValueError(f"Output path exists but is not a directory: {path}")

    SETTINGS.output_dir = to_pandoc_path(path)
    SETTINGS.docx_dir = to_pandoc_path(path / 'docx')
    SETTINGS.latex_dir = to_pandoc_path(path / 'latex')
    SETTINGS.html_dir = to_pandoc_path(path / 'html')
    SETTINGS.json_dir = to_pandoc_path(path / 'json')


def configure_output_file(output_file: str | Path | None) -> None:
    """Configure an exact manuscript output file and use its parent as output root."""
    if output_file is None:
        SETTINGS.output_file = None
        return

    path = Path(output_file)
    SETTINGS.output_file = to_pandoc_path(path)
    configure_output_dir(path.parent)


def configure_reference_doc(reference_doc: str | None) -> None:
    """Configure a user-supplied reference DOCX for DOCX-producing targets."""
    if reference_doc:
        SETTINGS.reference_doc = reference_doc


def manuscript_output_file(default_dir: str, suffix: str) -> Path:
    """Return the explicit output file, or the target's default derived file."""
    if SETTINGS.output_file:
        return Path(SETTINGS.output_file)
    return Path(default_dir) / f"{SETTINGS.project_name}.{suffix}"


def ensure_output_parent(output_file: Path) -> None:
    """Create the parent directory for an explicit or derived build output."""
    output_file.parent.mkdir(parents=True, exist_ok=True)


def should_use_style_metadata_file() -> bool:
    """Return True when the configured style metadata file exists for this build."""
    style_file = Path(SETTINGS.style_file)
    if not style_file.exists():
        log_info(f"[INFO] Style metadata file not found, skipping: {style_file}")
        return False
    return True


def style_metadata_files() -> list[str]:
    """Return existing style metadata files in the same order Pandoc receives them."""
    if not should_use_style_metadata_file():
        return []
    return [SETTINGS.style_file]


def ensure_docx_target_writable(target: Path) -> None:
    """Fail early when an existing DOCX target is open or cannot be overwritten.

    Word commonly locks an opened DOCX on Windows. Pandoc would otherwise fail
    later with a less direct error, so probe write access before starting work.
    """
    if not target.exists():
        return
    if target.is_dir():
        raise RuntimeError(f"Target DOCX path is a directory and cannot be overwritten: {target}")

    try:
        with target.open('r+b'):
            pass
    except OSError as exc:
        lock_like_errors = {errno.EACCES, errno.EPERM}
        lock_like_winerrors = {5, 32, 33}
        if exc.errno in lock_like_errors or getattr(exc, 'winerror', None) in lock_like_winerrors:
            raise RuntimeError(
                "目标 DOCX 可能已经在 Word 中打开，或正被其他程序占用，当前无法写入。\n"
                f"请关闭后重试: {target}"
            ) from exc
        raise


def load_build_metadata() -> EffectiveMetadata:
    """Load separated Papper settings and effective Pandoc metadata once."""
    effective = load_effective_metadata(
        SETTINGS.manuscript_file,
        SETTINGS.style_file if should_use_style_metadata_file() else None,
        allow_missing_header=True,
    )
    if not effective.has_yaml_header:
        # Reply-style documents may omit manuscript YAML; keep style.yml defaults.
        log_info(f"[INFO] No YAML front matter found in {SETTINGS.manuscript_file}; using metadata files only")
    return effective


def docx_svg_to_png_filter_args() -> list[str]:
    """Return Pandoc args for the DOCX SVG-to-PNG image filter."""
    return svg_filter_helpers.svg_to_png_filter_args()


def docx_svg_embed_images_filter_args() -> list[str]:
    """Return Pandoc args for the DOCX self-contained SVG image filter."""
    return svg_filter_helpers.svg_embed_images_filter_args()


def docx_svg_base_dirs() -> list[Path]:
    """Return lookup roots shared by DOCX SVG filters."""
    manuscript_dir = Path(SETTINGS.manuscript_file).parent
    return svg_filter_helpers.unique_resolved_dirs([Path.cwd(), manuscript_dir])


def docx_svg_embed_images_filter_env(
    settings: PmtSettings,
    embed_images: bool | None = None,
) -> dict[str, str]:
    """Return environment settings consumed by the SVG child-image embedding filter."""
    return svg_filter_helpers.svg_embed_images_filter_env(
        docx_svg_base_dirs(),
        settings,
        embed_images=embed_images,
    )


def docx_svg_to_png_filter_env(settings: PmtSettings, convert_all: bool | None = None) -> dict[str, str]:
    """Return environment settings consumed by the SVG-to-PNG Pandoc filter."""
    return svg_filter_helpers.svg_to_png_filter_env(
        docx_svg_base_dirs(),
        settings,
        convert_all=convert_all,
    )


def docx_metadata_filter_args() -> list[str]:
    """Return Pandoc args for DOCX-only hidden metadata markers."""
    filter_path = resource_path("pandoc/filters/docx/docx_metadata.lua")
    if not filter_path.exists():
        raise FileNotFoundError(f"DOCX metadata Pandoc filter not found: {filter_path}")
    return ["--lua-filter", to_pandoc_path(filter_path)]


def mathtype_marked_docx_path() -> Path:
    """Return the intermediate DOCX path that carries hidden LaTeX markers."""
    work_dir = Path(SETTINGS.mathtype_work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    # Keep the marker DOCX out of the final output directory but preserve it for
    # debugging failed conversions.
    return work_dir / f"{SETTINGS.project_name}.marked.docx"


def resolve_mathtype_build_enabled(requested: bool, conversion_method: object | None = None) -> bool:
    """Return whether this build should actually run MathType conversion.

    Users may keep `mathtype: true` in shared style metadata on machines that
    do not have MathType installed. In that case, continue with normal
    Pandoc/Word equations instead of failing the whole DOCX build.
    """
    if not requested:
        return False

    log_debug("[DEBUG] MathType DOCX equations enabled by metadata: mathtype: true")
    availability = check_mathtype_availability(conversion_method)
    if availability.usable:
        return True

    log_warning(
        availability.format_failure(
            "[WARN] MathType was requested by metadata, but MathType conversion will be skipped."
        )
    )
    log_warning("[WARN] Building DOCX with Pandoc/Word equations instead.\n")
    return False


def run_mathtype_conversion(marked_docx: Path, target_docx: Path, pmt_settings: PmtSettings) -> None:
    """Convert a marked DOCX's OMML equations into MathType OLE equations."""
    log_debug("[DOCX] Converting equations to MathType OLE objects...")
    convert_marked_docx(
        source=marked_docx,
        target=target_docx,
        work_dir=Path(SETTINGS.mathtype_work_dir) / SETTINGS.project_name,
        pmt_settings=pmt_settings,
    )


def generated_pandoc_metadata_file(
    effective: EffectiveMetadata,
    *,
    sync_docx_layout: bool,
    use_mathtype: bool = False,
) -> Path:
    """Write only Pandoc-facing metadata, optionally syncing DOCX equation tabs."""
    metadata = effective.pandoc_metadata
    tab_stops = None
    if sync_docx_layout:
        metadata = derive_docx_equation_layout(
            metadata,
            use_mathtype=use_mathtype,
        )
        metadata, tab_stops = sync_eqn_block_template_with_page_margins(
            metadata,
            effective.pmt_settings,
        )
    metadata_dir = PMT_WORK_DIR / "metadata"
    suffix = "docx" if sync_docx_layout else "pandoc"
    metadata_file = write_pandoc_metadata(
        metadata,
        metadata_dir / f"pandoc.{suffix}.generated.yml",
    )
    if tab_stops is not None:
        center_tab, right_tab = tab_stops
        log_debug(
            "[DEBUG] Synced eqnBlockTemplate tab stops from docxPageMargins: "
            f"center={center_tab}, right={right_tab}"
        )
    return metadata_file


def default_docx_csl() -> Path:
    """Return the bundled DOCX CSL used when project metadata does not pick one."""
    return resource_path(DEFAULT_DOCX_CSL)


def pandoc_filter_env(pmt_settings: PmtSettings) -> dict[str, str]:
    """Return Papper settings passed to bundled Pandoc filters via the environment."""
    delimiter = pmt_settings.citation_number_range_delimiter
    # Pandoc citeproc already emits an en dash. Passing it through Lua's Windows
    # environment boundary corrupts the Unicode value before the filter reads it.
    if delimiter is None or delimiter == "–":
        return {}
    return {PMT_CITATION_NUMBER_RANGE_DELIMITER_ENV: delimiter}


def run_pandoc(
    defaults_file: Path,
    output_file: Path,
    effective: EffectiveMetadata,
    extra_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    sync_docx_layout: bool = False,
    use_mathtype: bool = False,
    input_file: str | Path | None = None,
    default_csl: Path | None = None,
) -> None:
    """Run Pandoc with original defaults so ${.} resolves beside that file."""
    extra_args = extra_args or []
    cmd = [
        pandoc_command(),
        '--defaults',
        str(defaults_file),
        *style_metadata_args(
            effective,
            sync_docx_layout=sync_docx_layout,
            use_mathtype=use_mathtype,
        ),
        *csl_args(effective.pandoc_metadata, default_csl=default_csl),
        '--output',
        to_pandoc_path(output_file),
        *extra_args,
        to_pandoc_path(Path(input_file)) if input_file is not None else SETTINGS.manuscript_file,
    ]
    filter_env = pandoc_filter_env(effective.pmt_settings)
    run_command(
        cmd,
        stream_output=True,
        env=pandoc_tools_env({**(extra_env or {}), **filter_env}),
    )


def reference_doc_args() -> list[str]:
    """Return Pandoc args that override the bundled DOCX reference document."""
    if not SETTINGS.reference_doc:
        return []
    return ['--reference-doc', to_pandoc_path(Path(SETTINGS.reference_doc))]


def active_reference_doc() -> Path:
    """Return the source reference DOCX selected for this build."""
    if SETTINGS.reference_doc:
        return Path(SETTINGS.reference_doc)
    return resource_path("pandoc/manuscript-template/reference-doc.docx")


def generated_reference_doc_path() -> Path:
    """Return the temporary reference DOCX path prepared for Pandoc."""
    reference_dir = PMT_WORK_DIR / "reference-doc"
    return reference_dir / f"{SETTINGS.project_name}.reference.docx"


def docx_reference_doc_args(settings: PmtSettings) -> list[str]:
    """Return the reference-doc argument, applying docxPageMargins before Pandoc."""
    source = active_reference_doc()
    target = generated_reference_doc_path()
    result = write_reference_doc_with_page_margins(source, target, settings)
    if result is None:
        return reference_doc_args()

    log_debug(
        "[DEBUG] Prepared reference DOCX with docxPageMargins: "
        f"{to_pandoc_path(target)} margins={result['margins']}"
    )
    return ['--reference-doc', to_pandoc_path(target)]


def style_metadata_args(
    effective: EffectiveMetadata,
    *,
    sync_docx_layout: bool = False,
    use_mathtype: bool = False,
) -> list[str]:
    """Return a generated metadata file containing no Papper-owned settings."""
    metadata_file = generated_pandoc_metadata_file(
        effective,
        sync_docx_layout=sync_docx_layout,
        use_mathtype=use_mathtype,
    )
    return ["--metadata-file", to_pandoc_path(metadata_file)]


def csl_args(pandoc_metadata: dict[str, Any], *, default_csl: Path | None = None) -> list[str]:
    """Return the effective --csl argument for the selected Pandoc defaults file.

    Pandoc 3.10 gives a defaults-file `csl` higher precedence than later
    `--csl` or `--metadata-file` values. Keep the DOCX default out of
    `pandoc-docx.yml` and inject it here so manuscript/style metadata can still
    override it.
    """
    csl = pandoc_metadata.get("csl")
    if csl:
        return ['--csl', str(csl)]
    return ['--csl', to_pandoc_path(default_csl or default_docx_csl())]


CHINESE_HEADING_FONT = {"western": "Times New Roman", "chinese": "黑体"}
CHINESE_DOCX_STYLES = {
    "标题": {"fontFamily": CHINESE_HEADING_FONT, "bold": False},
    "副标题": {"fontFamily": CHINESE_HEADING_FONT, "bold": False},
    "标题 1": {"fontFamily": CHINESE_HEADING_FONT, "fontSize": "小三", "bold": False},
    "标题 2": {"fontFamily": CHINESE_HEADING_FONT, "fontSize": "四号", "bold": False},
    "标题 3": {"fontFamily": CHINESE_HEADING_FONT, "bold": False},
    "题注": {"fontSize": "五号", "bold": False},
}


def is_chinese_language(language: object) -> bool:
    """Return whether a Pandoc language tag identifies Chinese text."""
    if not isinstance(language, str):
        return False
    normalized = language.strip().replace("_", "-").casefold()
    return normalized in {"zh", "zhcn"} or normalized.startswith("zh-")


def prepare_pandoc_language(
    effective: EffectiveMetadata,
    lang_override: str | None = None,
    *,
    remove_lang: bool = False,
) -> tuple[EffectiveMetadata, bool]:
    """Apply language-specific Pandoc metadata shared by DOCX and HTML builds."""
    if lang_override is not None:
        normalized_override = lang_override.strip().replace("_", "-").casefold()
        if normalized_override not in {"zh-cn", "zhcn"}:
            raise ValueError("Only `--lang zh-cn` and `--lang zhcn` are currently supported for builds.")
        selected_language = normalized_override
    else:
        selected_language = effective.pandoc_metadata.get("lang")

    pandoc_metadata = dict(effective.pandoc_metadata)
    if remove_lang:
        pandoc_metadata.pop("lang", None)

    chinese_mode = is_chinese_language(selected_language)
    if chinese_mode:
        pandoc_metadata["chapters"] = True
        pandoc_metadata["chaptersDepth"] = 1
        pandoc_metadata["chapDelim"] = "-"
        pandoc_metadata["figureTitle"] = "图 "
        pandoc_metadata["tableTitle"] = "表 "
        pandoc_metadata["figPrefix"] = "图"
        pandoc_metadata["tblPrefix"] = "表"
        pandoc_metadata["titleDelim"] = " "
        pandoc_metadata["secPrefix"] = "节"
        pandoc_metadata["eqnPrefix"] = "式"
        pandoc_metadata["reference-section-title"] = "参考文献"

    return (
        EffectiveMetadata(
            pmt_settings=effective.pmt_settings,
            pandoc_metadata=pandoc_metadata,
            has_yaml_header=effective.has_yaml_header,
        ),
        chinese_mode,
    )


def prepare_docx_language(
    effective: EffectiveMetadata,
    lang_override: str | None = None,
) -> tuple[EffectiveMetadata, bool]:
    """Apply a one-build language mode and remove `lang` before Pandoc reads metadata."""
    effective, chinese_mode = prepare_pandoc_language(
        effective,
        lang_override,
        remove_lang=True,
    )
    pandoc_metadata = effective.pandoc_metadata
    pmt_settings = effective.pmt_settings.model_copy(deep=True)
    if chinese_mode:
        # Older project templates stored '连续' explicitly; treat that stock value as a default.
        if (
            "docx_show_line_numbers" not in pmt_settings.model_fields_set
            or pmt_settings.docx_show_line_numbers == "连续"
        ):
            pmt_settings.docx_show_line_numbers = False
        pmt_settings.docx_style = merge_metadata(
            pmt_settings.docx_style or {},
            CHINESE_DOCX_STYLES,
        )

    return (
        EffectiveMetadata(
            pmt_settings=pmt_settings,
            pandoc_metadata=pandoc_metadata,
            has_yaml_header=effective.has_yaml_header,
        ),
        chinese_mode,
    )


def build_docx(
    effective: EffectiveMetadata,
    *,
    warn_hat_order: bool = True,
    lang: str | None = None,
) -> None:
    """Generate DOCX file with optional post-processing."""
    log_debug("[DOCX] Building DOCX...\n")

    metadata_language = effective.pandoc_metadata.get("lang")
    effective, chinese_mode = prepare_docx_language(effective, lang)
    if lang is not None:
        log_debug(f"[DEBUG] Command-line DOCX language mode: {lang}")
    elif metadata_language is not None:
        log_debug(f"[DEBUG] DOCX language mode from Pandoc metadata: {metadata_language}")
    if chinese_mode:
        log_debug("[DEBUG] Chinese DOCX mode enabled: chapter-numbered figures/tables and non-bold heading styles")

    docx_file = manuscript_output_file(SETTINGS.docx_dir, "docx")
    ensure_output_parent(docx_file)
    ensure_docx_target_writable(docx_file)
    extra_args = []
    pmt_settings = effective.pmt_settings
    conversion_method = normalize_conversion_method(pmt_settings.mathtype_conversion_method)
    use_mathtype = resolve_mathtype_build_enabled(
        pmt_settings.mathtype,
        conversion_method,
    )
    if use_mathtype and warn_hat_order:
        warn_mathtype_hat_style_order(Path(SETTINGS.manuscript_file))
    pandoc_output = docx_file
    pandoc_env = {}
    if chinese_mode:
        # The language metadata is removed from the temporary DOCX input, so
        # pass the selected language mode explicitly to the shared AST filter.
        pandoc_env["PMT_CHINESE_MODE"] = "true"
    extra_args.extend(docx_reference_doc_args(pmt_settings))
    extra_args.extend(docx_metadata_filter_args())

    embed_svg_images = should_embed_docx_svg_images(pmt_settings)
    convert_all_svg = should_convert_docx_svg_to_png(pmt_settings)
    if convert_all_svg and svg_filter_helpers.requested_docx_svg_image_embedding(pmt_settings):
        log_debug("[DEBUG] Skipping SVG child-image embedding because docxConvertSvgToPng is enabled")
    elif embed_svg_images:
        log_debug("[DEBUG] Embedding linked child images inside SVG files for DOCX")
    extra_args.extend(docx_svg_embed_images_filter_args())
    pandoc_env.update(docx_svg_embed_images_filter_env(pmt_settings, embed_images=embed_svg_images))

    if convert_all_svg:
        log_debug("[DEBUG] Converting referenced SVG images to PNG for DOCX")
    extra_args.extend(docx_svg_to_png_filter_args())
    pandoc_env.update(docx_svg_to_png_filter_env(pmt_settings, convert_all=convert_all_svg))

    # Add filter for older Pandoc versions
    if should_use_mathbfit_filter():
        log_debug("[DEBUG] Using mathbfit filter (Pandoc <= 3.8.3.0)")
        extra_args.extend(['--filter', to_pandoc_path(python_filter_wrapper(
            resource_path('pandoc/filters/docx/to_mathbfit.py'), 'to_mathbfit_filter',
        ))])

    if use_mathtype:
        pandoc_output = mathtype_marked_docx_path()
        pandoc_env["PMT_ENABLE_MATHTYPE_MARKERS"] = "true"

    # Run pandoc
    sanitized_input = write_markdown_without_lang(SETTINGS.manuscript_file)
    try:
        run_pandoc(
            resource_path('pandoc/pandoc-docx.yml'),
            pandoc_output,
            effective,
            extra_args=extra_args,
            extra_env=pandoc_env,
            sync_docx_layout=True,
            use_mathtype=use_mathtype,
            input_file=sanitized_input,
            default_csl=resource_path(DEFAULT_CHINESE_DOCX_CSL) if chinese_mode else None,
        )
    finally:
        if sanitized_input is not None:
            sanitized_input.unlink(missing_ok=True)

    # Post-process DOCX if enabled
    if SETTINGS.enable_docx_postprocess:
        log_debug("\n[DOCX] Running Python post-processing...\n")
        postprocess_target = pandoc_output if use_mathtype else docx_file
        # When MathType is enabled, post-process the marker DOCX before
        # replacing OMML. Several DOCX fixes detect equation layout tables from
        # OMML, which is gone after OLE conversion.
        if not run_docx_postprocess(
            str(postprocess_target),
            pmt_settings=pmt_settings,
            pandoc_metadata=effective.pandoc_metadata,
        ):
            raise RuntimeError("DOCX post-processing failed")

    if use_mathtype:
        run_mathtype_conversion(pandoc_output, docx_file, pmt_settings)

    # Final output validation should inspect the real shipped DOCX rather than
    # an intermediate pre-MathType file, so syntax residue cannot slip through.
    syntax_findings = validate_final_docx_syntax(docx_file)
    if syntax_findings:
        raise RuntimeError("Final DOCX still contains unrendered Pandoc syntax")

    log_success(f"[OK] DOCX created: {docx_file}")


def build_latex():
    """Generate LaTeX file."""
    log_info("\n[LaTeX] Building LaTeX...\n")

    latex_file = manuscript_output_file(SETTINGS.latex_dir, "tex")
    ensure_output_parent(latex_file)

    # Run pandoc
    effective = load_build_metadata()
    run_pandoc(resource_path('pandoc/pandoc-latex.yml'), latex_file, effective)

    log_success(f"\n[OK] LaTeX created: {latex_file}")


def build_json():
    """Generate Pandoc JSON AST for debugging filters and metadata."""
    log_info("\n[JSON] Building Pandoc JSON AST...\n")

    json_file = manuscript_output_file(SETTINGS.json_dir, "json")
    ensure_output_parent(json_file)

    # Reuse the DOCX defaults because they carry the normal crossref/citeproc
    # pipeline users most often need to inspect when debugging manuscript builds.
    effective = load_build_metadata()
    run_pandoc(
        resource_path('pandoc/pandoc-docx.yml'),
        json_file,
        effective,
        extra_args=['--to', 'json'],
    )

    log_success(f"\n[OK] JSON created: {json_file}")


def run_build_command(
    *,
    target: str = "docx",
    markdown: str | None = None,
    manuscript_option: str | None = None,
    output_file: str | None = None,
    reference_doc: str | None = None,
    warn_hat_order: bool = True,
    mathtype: bool | None = None,
    lang: str | None = None,
    start_server: bool = False,
    server_host: str = DEFAULT_SERVER_HOST,
    server_port: int = DEFAULT_SERVER_PORT,
    server_command: str | None = None,
) -> int:
    """Run the selected manuscript build target with direct settings values."""
    configure_output_file(None)
    if target not in {"docx", "latex", "html", "json"}:
        raise ValueError(f"Unsupported build target: {target}")
    if output_file and target not in {"docx", "latex", "html", "json"}:
        raise ValueError("--output-file is only supported by the docx, latex, html, and json targets.")
    if output_file:
        configure_output_file(output_file)

    if markdown and manuscript_option:
        raise ValueError("Specify the markdown file either positionally or with --manuscript, not both.")

    manuscript_arg = manuscript_option or markdown
    if reference_doc and target != "docx":
        raise ValueError("--reference-doc is only supported by the docx target.")
    if mathtype is not None and target != "docx":
        raise ValueError("--mathtype/--no-mathtype is only supported by the docx target.")
    if lang is not None and target != "docx":
        raise ValueError("--lang is only supported by the docx target.")
    if start_server and target != "html":
        raise ValueError("--start-server is only supported by the html target.")
    configure_reference_doc(reference_doc)

    configure_manuscript(manuscript_arg or SETTINGS.manuscript_file, derive_project_name=bool(manuscript_arg))

    try:
        if target == "docx":
            effective = load_build_metadata()
            if mathtype is not None:
                effective.pmt_settings.mathtype = mathtype
                log_debug(f"[DEBUG] Command-line MathType setting: mathtype: {str(mathtype).lower()}")
            build_docx(effective=effective, warn_hat_order=warn_hat_order, lang=lang)
        elif target == "latex":
            build_latex()
        elif target == "html":
            build_html(
                start_server=start_server,
                server_host=server_host,
                server_port=server_port,
                server_command=server_command,
            )
        else:
            build_json()
        return 0
    except KeyboardInterrupt:
        log_warning("\n\n[WARN] Build interrupted by user.")
        return 1
    except Exception as e:
        log_error(f"\n[ERROR] {e}")
        return 1
