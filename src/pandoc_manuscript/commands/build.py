"""
Build manuscript targets through Papper's Pandoc workflow.
"""

import os
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, CliPositionalArg, CliSuppress, PydanticBaseSettingsSource, SettingsConfigDict

from ..runtime.logging import log_error, log_info, log_success, log_warning, log_debug
from ..runtime.metadata import (
    EffectiveMetadata,
    PmtSettings,
    load_effective_metadata,
    write_markdown_without_yaml_header,
    write_pandoc_metadata,
)
from ..runtime.paths import (
    PMT_MATHTYPE_WORK_DIR,
    PMT_WORK_DIR,
    pmt_path,
)
from ..runtime.resources import package_resource_path, template_root
from ..html.build import build_html
from .pandoc_server import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT
from .setup import pandoc_command, pandoc_tools_env
from .common import VerboseCommandSettings, project_directory, run_streaming_command

# ============================================================================
# SETTINGS
# ============================================================================


DEFAULT_OUTPUT_DIR = "output"
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


def load_build_metadata(lang_override: str | None = None) -> EffectiveMetadata:
    """Load separated Papper settings and effective Pandoc metadata once."""
    effective = load_effective_metadata(
        SETTINGS.manuscript_file,
        SETTINGS.style_file if should_use_style_metadata_file() else None,
        allow_missing_header=True,
        lang_override=lang_override,
        csl_resolver=lambda relative: to_pandoc_path(resource_path(relative)),
    )
    if not effective.has_yaml_header:
        # Reply-style documents may omit manuscript YAML; keep style.yml defaults.
        log_info(f"[INFO] No YAML front matter found in {SETTINGS.manuscript_file}; using metadata files only")
    return effective


def generated_pandoc_metadata_file(
    effective: EffectiveMetadata,
) -> Path:
    """Write only shared Pandoc metadata for a non-DOCX build."""
    return write_pandoc_metadata(
        effective.pandoc_metadata,
        PMT_WORK_DIR / "metadata" / "pandoc.pandoc.generated.yml",
    )


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
    metadata_file: Path | None = None,
) -> None:
    """Run Pandoc with one generated metadata source and a header-free input copy."""
    extra_args = extra_args or []
    generated_input = write_markdown_without_yaml_header(SETTINGS.manuscript_file)
    pandoc_input = generated_input or Path(SETTINGS.manuscript_file)
    try:
        metadata_args = (
            ["--metadata-file", to_pandoc_path(metadata_file)]
            if metadata_file is not None
            else style_metadata_args(effective)
        )
        cmd = [
            pandoc_command(),
            '--defaults',
            str(defaults_file),
            *metadata_args,
            '--output',
            to_pandoc_path(output_file),
            *extra_args,
            to_pandoc_path(pandoc_input),
        ]
        filter_env = pandoc_filter_env(effective.pmt_settings)
        run_command(
            cmd,
            stream_output=True,
            env=pandoc_tools_env({**(extra_env or {}), **filter_env}),
        )
    finally:
        if generated_input is not None:
            generated_input.unlink(missing_ok=True)


def style_metadata_args(
    effective: EffectiveMetadata,
) -> list[str]:
    """Return a generated metadata file containing no Papper-owned settings."""
    metadata_file = generated_pandoc_metadata_file(effective)
    return ["--metadata-file", to_pandoc_path(metadata_file)]


def build_docx(
    effective: EffectiveMetadata,
    *,
    warn_hat_order: bool = True,
    lang: str | None = None,
) -> None:
    """Delegate DOCX target orchestration to the DOCX package."""
    from ..docx.build import DocxBuildContext, build_docx as implementation

    context = DocxBuildContext(
        settings=SETTINGS,
        manuscript_output_file=manuscript_output_file,
        ensure_output_parent=ensure_output_parent,
        resource_path=resource_path,
        to_pandoc_path=to_pandoc_path,
        run_pandoc=run_pandoc,
    )
    implementation(effective, context, warn_hat_order=warn_hat_order, lang=lang)


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
            effective = load_build_metadata(lang_override=lang)
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
