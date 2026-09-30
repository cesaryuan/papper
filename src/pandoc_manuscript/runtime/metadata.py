"""Centralized loading and merging for Papper settings and Pandoc metadata."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Literal, Sequence

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .logging import log_warning
from .resources import project_template_root


class MissingYamlFrontMatterError(ValueError):
    """Raised when a markdown file does not start with a YAML front matter block."""


def merge_metadata(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge metadata so later sources override earlier defaults."""
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_metadata(existing, value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=2)
def bundled_style_path(chinese: bool) -> Path:
    """Locate the packaged language default used by manuscript builds."""
    name = "style-cn.yml" if chinese else "style.yml"
    path = project_template_root() / name
    if not path.is_file():
        raise FileNotFoundError(f"Bundled style defaults not found: {path}")
    return path


def _bundled_style_mapping(chinese: bool) -> dict[str, Any]:
    """Read one packaged style YAML as a validated top-level mapping."""
    raw = yaml.safe_load(bundled_style_path(chinese).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Bundled style defaults must be a YAML mapping")
    return raw


def default_pandoc_metadata() -> dict[str, Any]:
    """Return an independent copy of the packaged English Pandoc defaults."""
    return deepcopy(_bundled_style_mapping(False).get("pandocMetadata", {}))


def build_default_pandoc_metadata(
    language: object = None,
    *,
    csl_resolver: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Return packaged language defaults and optionally resolve their CSL path."""
    defaults = deepcopy(_bundled_style_mapping(is_chinese_language(language)).get("pandocMetadata", {}))
    if csl_resolver is not None and defaults.get("csl"):
        defaults["csl"] = csl_resolver(defaults["csl"])
    return defaults


def is_chinese_language(language: object) -> bool:
    """Return whether a Pandoc language tag identifies Chinese text."""
    if not isinstance(language, str):
        return False
    normalized = language.strip().replace("_", "-").casefold()
    return normalized in {"zh", "zhcn", "zh-hans", "zhhans"} or normalized.startswith("zh-")


DEFAULT_PANDOC_METADATA = default_pandoc_metadata()
DEFAULT_PANDOC_METADATA_ZHCN = build_default_pandoc_metadata("zh-Hans")
ENGLISH_DOCX_STYLES = deepcopy(_bundled_style_mapping(False).get("docxStyle", {}))
CHINESE_DOCX_STYLES = deepcopy(_bundled_style_mapping(True).get("docxStyle", {}))


def normalize_pandoc_language(language: object) -> object:
    """Map Simplified Chinese aliases to Pandoc-crossref's shipped tag."""
    if not isinstance(language, str):
        return language
    normalized = language.strip().replace("_", "-").casefold()
    if normalized in {"zh-cn", "zhcn", "zh-hans", "zhhans"}:
        return "zh-Hans"
    return language


PMT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "mathtype": ("mathtype",),
    "mathtypeConversionMethod": ("mathtypeConversionMethod", "mathtype-conversion-method", "mathtype_conversion_method"),
    "mathtypeTypstMathFont": ("mathtypeTypstMathFont", "mathtype-typst-math-font", "mathtype_typst_math_font"),
    "mathtypeTypstMathFont": ("mathtypeTypstMathFont", "mathtype-typst-math-font", "mathtype_typst_math_font"),
    "mathtypeSvgBackend": ("mathtypeSvgBackend", "mathtype-svg-backend", "mathtype_svg_backend"),
    "docxEmbedSvgImages": (
        "docxEmbedSvgImages",
        "docx-embed-svg-images",
        "docx_embed_svg_images",
        "embedSvgImages",
        "embed-svg-images",
    ),
    "docxConvertSvgToPng": (
        "docxConvertSvgToPng",
        "docx-convert-svg-to-png",
        "docx_convert_svg_to_png",
        "convertSvgToPng",
        "convert-svg-to-png",
    ),
    "docxNativeCrossref": ("docxNativeCrossref", "docx-native-crossref", "docx_native_crossref"),
    "docxSvgToPngWidth": ("docxSvgToPngWidth", "docx-svg-to-png-width", "docx_svg_to_png_width"),
    "docxSvgToPngDpi": ("docxSvgToPngDpi", "docx-svg-to-png-dpi", "docx_svg_to_png_dpi"),
    "docxSvgToPngScale": ("docxSvgToPngScale", "docx-svg-to-png-scale", "docx_svg_to_png_scale"),
    "citationNumberRangeDelimiter": (
        "citationNumberRangeDelimiter",
        "citation-number-range-delimiter",
        "citation_number_range_delimiter",
    ),
    "docxShowLineNumbers": (
        "docxShowLineNumbers",
        "docx-show-line-numbers",
        "docx_show_line_numbers",
        "show-line-numbers",
        "showLineNumbers",
        "show_line_numbers",
    ),
    "docxShowPageNumbers": (
        "docxShowPageNumbers",
        "docx-show-page-numbers",
        "docx_show_page_numbers",
        "show-page-numbers",
        "showPageNumbers",
        "show_page_numbers",
    ),
    "docxPageMargins": ("docxPageMargins", "docx-page-margins", "docx_page_margins"),
    "docxPageWidth": ("docxPageWidth", "docx-page-width", "docx_page_width"),
    "docxStyle": ("docxStyle", "docx-style", "docx_style"),
}
LEGACY_BODY_TEXT_ALIASES = ("bodyText", "body-text", "body_text", "docxBodyText", "docx-body-text")
PMT_ALIAS_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in PMT_FIELD_ALIASES.items()
    for alias in aliases
}


def _split_style_mapping(
    raw: dict[str, Any],
    *,
    source: Path,
    section: str = "style.yml",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Split one style mapping into Papper fields, Pandoc metadata, and reply overrides."""
    pmt_values: dict[str, Any] = {}
    legacy_pandoc: dict[str, Any] = {}
    legacy_body_text: dict[str, Any] | None = None
    explicit_pandoc = raw.get("pandocMetadata", {})
    if not isinstance(explicit_pandoc, dict):
        raise ValueError(f"`pandocMetadata` in {source} ({section}) must be a YAML mapping")
    explicit_pandoc = dict(explicit_pandoc)
    legacy_delimiter = explicit_pandoc.pop("citation-number-range-delimiter", None)

    reply = raw.get("reply")
    if reply is not None and not isinstance(reply, dict):
        raise ValueError(f"`reply` in {source} ({section}) must be a YAML mapping")

    for key, value in raw.items():
        if key in {"pandocMetadata", "reply"}:
            continue
        canonical = PMT_ALIAS_TO_CANONICAL.get(key)
        if key in LEGACY_BODY_TEXT_ALIASES:
            if not isinstance(value, dict):
                raise ValueError(f"`{key}` in {source} ({section}) must be a YAML mapping")
            legacy_body_text = value
            continue
        if canonical is None:
            legacy_pandoc[key] = value
        else:
            pmt_values[canonical] = value

    if legacy_body_text is not None:
        explicit_styles = pmt_values.get("docxStyle", {})
        pmt_values["docxStyle"] = merge_metadata(
            {"正文文本": legacy_body_text},
            explicit_styles,
        )
    if legacy_delimiter is not None:
        if "citationNumberRangeDelimiter" not in pmt_values:
            pmt_values["citationNumberRangeDelimiter"] = legacy_delimiter
        log_warning(
            f"[WARN] Deprecated pandocMetadata.citation-number-range-delimiter in {source} ({section}). "
            "Move it to top-level citationNumberRangeDelimiter."
        )

    if legacy_pandoc:
        keys = ", ".join(sorted(legacy_pandoc))
        conflicts = sorted(set(legacy_pandoc) & set(explicit_pandoc))
        conflict_note = ""
        if conflicts:
            conflict_note = f" Conflicts use pandocMetadata values: {', '.join(conflicts)}."
        log_warning(
            f"[WARN] Deprecated flat Pandoc metadata in {source} ({section}): {keys}. "
            f"Move these keys under pandocMetadata.{conflict_note}"
        )

    pmt_values["pandocMetadata"] = merge_metadata(legacy_pandoc, explicit_pandoc)
    pmt_values["reply"] = reply
    return pmt_values, pmt_values["pandocMetadata"], reply


class ReplySettings(BaseModel):
    """Validated reply-specific overrides for both configuration domains."""

    model_config = ConfigDict(extra="forbid")

    pmt_overrides: dict[str, Any] = Field(default_factory=dict)
    pandoc_metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], source: Path) -> "ReplySettings":
        """Split and validate one reply override mapping."""
        values, pandoc_metadata, _ = _split_style_mapping(
            raw,
            source=source,
            section="reply",
        )
        pmt_values = {
            key: value
            for key, value in values.items()
            if key not in {"pandocMetadata", "reply"}
        }
        validated = PmtSettings.model_validate(pmt_values)
        return cls(
            pmt_overrides=validated.to_mapping(exclude_unset=True),
            pandoc_metadata=pandoc_metadata,
        )


class PmtSettings(BaseSettings):
    """Typed Papper-owned settings loaded from the top level of style.yml."""

    model_config = SettingsConfigDict(extra="forbid", populate_by_name=True)

    mathtype: bool = Field(
        default=False,
        description=(
            "Convert DOCX equations to MathType OLE objects during the build. "
            "When disabled, keep native Word equations and require no local MathType/OLE environment."
        ),
    )
    mathtype_conversion_method: str = Field(
        default="auto",
        validation_alias=AliasChoices("mathtypeConversionMethod", "mathtype-conversion-method", "mathtype_conversion_method"),
        serialization_alias="mathtypeConversionMethod",
    )
    mathtype_svg_backend: Literal["ratex", "typst"] = Field(
        default="typst",
        validation_alias=AliasChoices("mathtypeSvgBackend", "mathtype-svg-backend", "mathtype_svg_backend"),
        serialization_alias="mathtypeSvgBackend",
    )
    mathtype_typst_math_font: str = Field(
        default="New Computer Modern Math",
        validation_alias=AliasChoices("mathtypeTypstMathFont", "mathtype-typst-math-font", "mathtype_typst_math_font"),
        serialization_alias="mathtypeTypstMathFont",
    )
    docx_native_crossref: bool = Field(
        default=False,
        validation_alias=AliasChoices("docxNativeCrossref", "docx-native-crossref", "docx_native_crossref"),
        serialization_alias="docxNativeCrossref",
        description="Use native Word REF/SEQ fields and multilevel heading numbering in manuscript DOCX builds.",
    )  # False preserves Pandoc numbering and hyperlink cross-references
    docx_embed_svg_images: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "docxEmbedSvgImages", "docx-embed-svg-images", "docx_embed_svg_images", "embedSvgImages", "embed-svg-images"
        ),
        serialization_alias="docxEmbedSvgImages",
    )
    docx_convert_svg_to_png: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "docxConvertSvgToPng", "docx-convert-svg-to-png", "docx_convert_svg_to_png", "convertSvgToPng", "convert-svg-to-png"
        ),
        serialization_alias="docxConvertSvgToPng",
    )
    docx_svg_to_png_width: int | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("docxSvgToPngWidth", "docx-svg-to-png-width", "docx_svg_to_png_width"),
        serialization_alias="docxSvgToPngWidth",
    )
    docx_svg_to_png_dpi: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("docxSvgToPngDpi", "docx-svg-to-png-dpi", "docx_svg_to_png_dpi"),
        serialization_alias="docxSvgToPngDpi",
    )
    docx_svg_to_png_scale: float | None = Field(
        default=None,
        gt=0,
        validation_alias=AliasChoices("docxSvgToPngScale", "docx-svg-to-png-scale", "docx_svg_to_png_scale"),
        serialization_alias="docxSvgToPngScale",
    )
    citation_number_range_delimiter: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "citationNumberRangeDelimiter",
            "citation-number-range-delimiter",
            "citation_number_range_delimiter",
        ),
        serialization_alias="citationNumberRangeDelimiter",
    )
    docx_show_line_numbers: bool | str = Field(
        default="continuous",
        validation_alias=AliasChoices(
            "docxShowLineNumbers",
            "docx-show-line-numbers",
            "docx_show_line_numbers",
            "show-line-numbers",
            "showLineNumbers",
            "show_line_numbers",
        ),
        serialization_alias="docxShowLineNumbers",
    )
    docx_show_page_numbers: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "docxShowPageNumbers",
            "docx-show-page-numbers",
            "docx_show_page_numbers",
            "show-page-numbers",
            "showPageNumbers",
            "show_page_numbers",
        ),
        serialization_alias="docxShowPageNumbers",
    )
    docx_page_margins: dict[str, str | int | float] | None = Field(
        default=None,
        validation_alias=AliasChoices("docxPageMargins", "docx-page-margins", "docx_page_margins"),
        serialization_alias="docxPageMargins",
    )
    docx_page_width: str | int | float | None = Field(
        default=None,
        validation_alias=AliasChoices("docxPageWidth", "docx-page-width", "docx_page_width"),
        serialization_alias="docxPageWidth",
    )
    docx_style: dict[str, dict[str, Any]] | None = Field(
        default=None,
        validation_alias=AliasChoices("docxStyle", "docx-style", "docx_style"),
        serialization_alias="docxStyle",
    )
    pandoc_metadata: dict[str, Any] = Field(
        default_factory=dict,
        alias="pandocMetadata",
    )
    reply: ReplySettings | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Disable implicit sources because manuscript builds must be deterministic."""
        return (init_settings,)

    @field_validator("mathtype_typst_math_font")
    @classmethod
    def validate_typst_math_font(cls, value: str) -> str:
        """Reject empty font families before starting formula conversion."""
        value = value.strip()
        if not value:
            raise ValueError("mathtypeTypstMathFont must not be blank")
        return value

    @field_validator("mathtype_conversion_method")
    @classmethod
    def validate_conversion_method(cls, value: str) -> str:
        """Normalize supported MathType method aliases at the settings boundary."""
        normalized = value.strip().casefold().replace("_", "-")
        aliases = {
            "rust": "rust",
            "mathtype-rust": "rust",
            "mtef": "rust",
            "rust-sdk": "rust-sdk",
            "sdk": "rust-sdk",
            "sdk-xform-ole": "rust-sdk",
            "set-data": "set-data",
            "setdata": "set-data",
            "auto": "auto",
            "both": "both",
        }
        if normalized not in aliases:
            supported = ", ".join(("rust", "rust-sdk", "set-data", "auto", "both"))
            raise ValueError(f"unsupported MathType conversion method; expected one of: {supported}")
        return aliases[normalized]

    @model_validator(mode="after")
    def validate_svg_size_control(self) -> "PmtSettings":
        """Reject ambiguous global SVG rasterization size controls."""
        configured = [
            name
            for name, value in (
                ("docxSvgToPngWidth", self.docx_svg_to_png_width),
                ("docxSvgToPngScale", self.docx_svg_to_png_scale),
                ("docxSvgToPngDpi", self.docx_svg_to_png_dpi),
            )
            if value is not None
        ]
        if len(configured) > 1:
            raise ValueError(
                "Only one of docxSvgToPngWidth, docxSvgToPngScale, "
                f"docxSvgToPngDpi can be set; got: {', '.join(configured)}"
            )
        return self

    @classmethod
    def load(cls, style_path: str | Path) -> "PmtSettings":
        """Load only explicit settings and metadata from one style file."""
        path = Path(style_path)
        try:
            source = YamlConfigSettingsSource(cls, yaml_file=path, yaml_file_encoding="utf-8")
            raw = source()
            if not isinstance(raw, dict):
                raise ValueError("YAML root must be a mapping")
            pmt_values, _, reply = _split_style_mapping(
                raw,
                source=path,
            )
            pmt_values["reply"] = ReplySettings.from_mapping(reply, path) if reply is not None else None
            settings = cls.model_validate(pmt_values)
            font = settings.mathtype_typst_math_font
            font_path = Path(font).expanduser()
            if "/" in font or "\\" in font or font_path.suffix.lower() in {".otf", ".ttf", ".ttc", ".otc"}:
                # Resolve font assets beside their style file, independent of build cwd.
                if not font_path.is_absolute():
                    font_path = path.parent / font_path
                settings.mathtype_typst_math_font = str(font_path.resolve())
            return settings
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid style settings in {path}: {exc}") from exc

    def to_mapping(self, *, exclude_unset: bool = False) -> dict[str, Any]:
        """Return canonical Papper fields for existing dictionary-based consumers."""
        return self.model_dump(
            by_alias=True,
            exclude={"pandoc_metadata", "reply"},
            exclude_none=True,
            exclude_unset=exclude_unset,
        )

    def for_reply(self, source: str | Path = "style.yml") -> "PmtSettings":
        """Return settings with the optional reply section applied recursively."""
        if not self.reply:
            return self.model_copy(deep=True)
        base = self.to_mapping()
        merged = merge_metadata(base, self.reply.pmt_overrides)
        merged["pandocMetadata"] = merge_metadata(
            self.pandoc_metadata,
            self.reply.pandoc_metadata,
        )
        merged["reply"] = None
        return type(self).model_validate(merged)


@dataclass(frozen=True)
class EffectiveMetadata:
    """Keep Papper settings separate from the metadata supplied to Pandoc."""

    pmt_settings: PmtSettings
    pandoc_metadata: dict[str, Any]
    has_yaml_header: bool


def parse_yaml_header(md_path: str | Path) -> dict[str, Any]:
    """Parse YAML front matter from a markdown manuscript file."""
    path = Path(md_path)
    content = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        raise MissingYamlFrontMatterError(f"No YAML front matter found in markdown file: {path}")
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"YAML front matter must be a mapping: {path}")
    return metadata


def parse_yaml_file(yaml_path: str | Path) -> dict[str, Any]:
    """Parse a standalone YAML metadata file."""
    path = Path(yaml_path)
    metadata = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"YAML metadata file must be a mapping: {path}")
    return metadata


def write_pandoc_metadata(metadata: dict[str, Any], output_path: str | Path) -> Path:
    """Write a generated YAML file containing only Pandoc-facing metadata."""
    path = Path(output_path)
    serialized = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
    # Repeat builds often produce identical metadata; preserve the existing file.
    if not path.is_file() or path.read_text(encoding="utf-8") != serialized:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")
    return path


def write_markdown_without_yaml_header(markdown_path: str | Path) -> Path | None:
    """Write a temporary source copy whose metadata comes only from `--metadata-file`."""
    source = Path(markdown_path).resolve()
    content = source.read_text(encoding="utf-8")
    match = re.match(
        r"\A(?P<opening>---[ \t]*\r?\n)(?P<header>.*?)(?P<closing>\r?\n---[ \t]*)(?P<newline>\r?\n|$)",
        content,
        re.DOTALL,
    )
    if match is None:
        return None
    # The header has already been parsed into effective metadata. Remove it
    # wholesale so Pandoc cannot merge a second, divergent metadata source.
    sanitized = content[match.end():]
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=f".{source.stem}.pmt-no-yaml-",
        suffix=source.suffix,
        dir=source.parent,
        delete=False,
    ) as temporary:
        temporary.write(sanitized)
    return Path(temporary.name)


def load_effective_metadata(
    manuscript_path: str | Path,
    style_path: str | Path | None = "style.yml",
    *,
    style_paths: Sequence[str | Path] | None = None,
    allow_missing_header: bool = False,
    reply: bool = False,
    lang_override: str | None = None,
    csl_resolver: Callable[[str], str] | None = None,
) -> EffectiveMetadata:
    """Select bundled YAML defaults, then overlay project and manuscript metadata."""
    candidate_styles = (
        [Path(item) for item in style_paths]
        if style_paths is not None
        else ([Path(style_path)] if style_path is not None else [])
    )
    # Merge from the lowest-priority source first.  The caller lists the
    # Markdown directory before the working directory, so reverse that order
    # to keep the Markdown-local style as the final override.
    existing_styles = [path for path in reversed(candidate_styles) if path.exists()]
    project_settings: PmtSettings | None = None
    for style in existing_styles:
        current = PmtSettings.load(style)
        if project_settings is None:
            project_settings = current
            continue
        merged_mapping = merge_metadata(
            project_settings.to_mapping(exclude_unset=True),
            current.to_mapping(exclude_unset=True),
        )
        merged_mapping["pandocMetadata"] = merge_metadata(
            project_settings.pandoc_metadata,
            current.pandoc_metadata,
        )
        project_settings = PmtSettings.model_validate(merged_mapping)
    try:
        manuscript_metadata = parse_yaml_header(manuscript_path)
        has_header = True
    except MissingYamlFrontMatterError:
        if not allow_missing_header:
            raise
        manuscript_metadata = {}
        has_header = False
    if "citation-number-range-delimiter" in manuscript_metadata:
        manuscript_metadata = dict(manuscript_metadata)
        del manuscript_metadata["citation-number-range-delimiter"]
        log_warning(
            f"[WARN] Ignoring deprecated manuscript metadata `citation-number-range-delimiter` in {manuscript_path}. "
            "Configure top-level style.yml `citationNumberRangeDelimiter` instead."
        )
    project_pandoc_metadata = dict(project_settings.pandoc_metadata) if project_settings else {}
    metadata_overrides = merge_metadata(project_pandoc_metadata, manuscript_metadata)
    # An empty CSL is not an override; preserve the previous fallback behavior.
    if not metadata_overrides.get("csl"):
        metadata_overrides.pop("csl", None)
        project_pandoc_metadata.pop("csl", None)
    selected_language = lang_override if lang_override is not None else metadata_overrides.get("lang")
    if lang_override is not None:
        normalized_override = lang_override.strip().replace("_", "-").casefold()
        if normalized_override not in {"zh-cn", "zhcn"}:
            raise ValueError("Only `--lang zh-cn` and `--lang zhcn` are currently supported for builds.")
    defaults = PmtSettings.load(bundled_style_path(is_chinese_language(selected_language)))
    settings_mapping = defaults.to_mapping(exclude_unset=True)
    pandoc_defaults = dict(defaults.pandoc_metadata)
    bundled_csl = pandoc_defaults.get("csl")
    if project_settings is not None:
        settings_mapping = merge_metadata(settings_mapping, project_settings.to_mapping(exclude_unset=True))
        pandoc_defaults = merge_metadata(pandoc_defaults, project_pandoc_metadata)
    if reply:
        # Reply overrides follow the project's base settings, even when the
        # project style is missing and the bundled reply defaults apply alone.
        for source in (defaults, project_settings):
            if source is not None and source.reply is not None:
                settings_mapping = merge_metadata(settings_mapping, source.reply.pmt_overrides)
                pandoc_defaults = merge_metadata(pandoc_defaults, source.reply.pandoc_metadata)
    settings = PmtSettings.model_validate(settings_mapping)
    pandoc_metadata = merge_metadata(pandoc_defaults, metadata_overrides)
    if csl_resolver is not None and bundled_csl and pandoc_metadata.get("csl") == bundled_csl:
        # Initialized projects copy the bundled YAML, including its relative CSL.
        pandoc_metadata["csl"] = csl_resolver(bundled_csl)
    if lang_override is not None or is_chinese_language(selected_language):
        # Pandoc-crossref ships zh-Hans rather than the common zh-CN alias.
        pandoc_metadata["lang"] = normalize_pandoc_language(selected_language)
    return EffectiveMetadata(
        pmt_settings=settings,
        pandoc_metadata=pandoc_metadata,
        has_yaml_header=has_header,
    )


def load_metadata_files(metadata_files: list[str | Path] | None = None) -> dict[str, Any]:
    """Load standalone metadata files and merge them in the given order."""
    metadata: dict[str, Any] = {}
    for metadata_file in metadata_files or []:
        path = Path(metadata_file)
        if not path.exists():
            raise FileNotFoundError(f"Metadata file not found: {path}")
        metadata = merge_metadata(metadata, parse_yaml_file(path))
    return metadata


def load_merged_metadata(
    md_path: str | Path,
    metadata_files: list[str | Path] | None = None,
    *,
    allow_missing_header: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper for standalone tools that merge generic metadata files."""
    metadata, _ = load_merged_metadata_with_status(
        md_path,
        metadata_files,
        allow_missing_header=allow_missing_header,
    )
    return metadata


def load_merged_metadata_with_status(
    md_path: str | Path,
    metadata_files: list[str | Path] | None = None,
    *,
    allow_missing_header: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Compatibility wrapper returning generic merged metadata and header status."""
    metadata = load_metadata_files(metadata_files)
    try:
        return merge_metadata(metadata, parse_yaml_header(md_path)), True
    except MissingYamlFrontMatterError:
        if allow_missing_header:
            return metadata, False
        raise
