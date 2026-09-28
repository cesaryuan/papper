"""DOCX-specific metadata adjustments layered on shared Pandoc defaults."""

from __future__ import annotations

from pathlib import Path

from ..runtime.logging import log_debug
from ..runtime.metadata import (
    EffectiveMetadata,
    CHINESE_DOCX_STYLES,
    PmtSettings,
    is_chinese_language,
    merge_metadata,
    write_pandoc_metadata,
)
from ..runtime.paths import PMT_WORK_DIR
from .equation_layout import derive_docx_equation_layout, sync_eqn_block_template_with_page_margins


def derive_docx_pandoc_metadata(
    effective: EffectiveMetadata,
    *,
    use_mathtype: bool,
) -> tuple[dict[str, object], tuple[int, int] | None]:
    """Prepare DOCX equation metadata and synchronize optional tab stops."""
    metadata = derive_docx_equation_layout(
        dict(effective.pandoc_metadata),
        use_mathtype=use_mathtype,
    )
    return sync_eqn_block_template_with_page_margins(
        metadata,
        effective.pmt_settings,
    )


def write_docx_pandoc_metadata(
    effective: EffectiveMetadata,
    *,
    use_mathtype: bool,
) -> Path:
    """Write a DOCX metadata file with the effective equation layout."""
    metadata, tab_stops = derive_docx_pandoc_metadata(
        effective,
        use_mathtype=use_mathtype,
    )
    output = write_pandoc_metadata(
        metadata,
        PMT_WORK_DIR / "metadata" / "pandoc.docx.generated.yml",
    )
    if tab_stops is not None:
        center_tab, right_tab = tab_stops
        log_debug(
            "[DEBUG] Synced eqnBlockTemplate tab stops from docxPageMargins: "
            f"center={center_tab}, right={right_tab}"
        )
    return output


def prepare_docx_metadata(
    effective: EffectiveMetadata,
) -> EffectiveMetadata:
    """Apply DOCX-only Chinese style defaults after shared metadata loads."""
    chinese_mode = is_chinese_language(effective.pandoc_metadata.get("lang"))
    pmt_settings: PmtSettings = effective.pmt_settings.model_copy(deep=True)
    if chinese_mode:
        # Older project templates stored '连续' explicitly; treat that stock
        # value as a default while preserving other explicit line-number modes.
        if (
            "docx_show_line_numbers" not in pmt_settings.model_fields_set
            or pmt_settings.docx_show_line_numbers == "连续"
        ):
            pmt_settings.docx_show_line_numbers = False
        # User style entries override only the Chinese defaults they mention.
        pmt_settings.docx_style = merge_metadata(
            CHINESE_DOCX_STYLES,
            pmt_settings.docx_style or {},
        )

    return EffectiveMetadata(
        pmt_settings=pmt_settings,
        pandoc_metadata=effective.pandoc_metadata,
        has_yaml_header=effective.has_yaml_header,
    )
