"""Apply DOCX line-number settings from merged YAML metadata."""

from typing import Any

from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from ...runtime.metadata import PmtSettings

DEFAULT_RESTART = "continuous"
RESTART_ALIASES = {
    "continuous": "continuous",
    "continue": "continuous",
    "continuously": "continuous",
    "true": "continuous",
    "on": "continuous",
    "yes": "continuous",
    "1": "continuous",
    "连续": "continuous",
    "restart-page": "newPage",
    "restart-each-page": "newPage",
    "new-page": "newPage",
    "newpage": "newPage",
    "page": "newPage",
    "每页": "newPage",
    "每页重编": "newPage",
    "按页重启": "newPage",
    "restart-section": "newSection",
    "restart-each-section": "newSection",
    "new-section": "newSection",
    "newsection": "newSection",
    "section": "newSection",
    "每节": "newSection",
    "每节重编": "newSection",
    "按节重启": "newSection",
}
DISABLED_VALUES = {"false", "off", "no", "0", "none", "disable", "disabled", "不显示", "关闭", "无"}


def normalize_line_number_setting(value: Any) -> str | None:
    """Normalize docxShowLineNumbers to a Word restart mode.

    This handles the common shorthand `docxShowLineNumbers: true` as continuous
    numbering, while string values select Word's line-number restart behavior.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return DEFAULT_RESTART if value else None
    if isinstance(value, (int, float)):
        return DEFAULT_RESTART if bool(value) else None
    if not isinstance(value, str):
        raise ValueError("docxShowLineNumbers must be a boolean or string")

    cleaned = value.strip()
    if not cleaned:
        return None

    lookup_key = cleaned.lower().replace("_", "-").replace(" ", "-")
    if lookup_key in DISABLED_VALUES:
        return None
    if cleaned in RESTART_ALIASES:
        return RESTART_ALIASES[cleaned]
    if lookup_key in RESTART_ALIASES:
        return RESTART_ALIASES[lookup_key]

    valid = "continuous, restart-page/newPage, restart-section/newSection, 连续, 每页重编, 每节重编"
    raise ValueError(f"Unsupported docxShowLineNumbers value: {value!r}. Expected one of: {valid}")


def line_number_setting_from_settings(settings: PmtSettings) -> str | None:
    """Return the normalized line-number mode from typed Papper settings."""
    return normalize_line_number_setting(settings.docx_show_line_numbers)


def get_or_add_line_number_type(sect_pr):
    """Return a section's w:lnNumType element, creating it in Word-compatible order."""
    existing = sect_pr.find(qn("w:lnNumType"))
    if existing is not None:
        return existing

    ln_num_type = OxmlElement("w:lnNumType")
    for tag in ("w:pgNumType", "w:cols", "w:formProt", "w:vAlign", "w:noEndnote"):
        anchor = sect_pr.find(qn(tag))
        if anchor is not None:
            anchor.addprevious(ln_num_type)
            return ln_num_type
    sect_pr.append(ln_num_type)
    return ln_num_type


def apply_line_numbers(doc: DocumentObject, restart: str) -> int:
    """Enable line numbers on every document section using the given restart mode."""
    updated = 0
    for section in doc.sections:
        ln_num_type = get_or_add_line_number_type(section._sectPr)
        ln_num_type.set(qn("w:countBy"), "1")
        ln_num_type.set(qn("w:restart"), restart)
        updated += 1
    return updated


def apply_line_number_settings(doc: DocumentObject, settings: PmtSettings) -> dict[str, Any] | None:
    """Apply typed line-number settings to a DOCX document."""
    restart = line_number_setting_from_settings(settings)
    if restart is None:
        return None
    return {
        "restart": restart,
        "sections": apply_line_numbers(doc, restart),
    }
