"""Apply 'Para Equation' to top-level equations arranged with tab stops.

Use the local OMML math/tab detectors, with OLE support for documents already
converted to MathType. Inherit Body Text and set half a line after and
single line spacing. Set center/right tabs from the first section's actual page
width and margins, removing direct tabs and conflicting spacing overrides.

Runs automatically in DOCX post-processing, or in place via:
    uv run python -m pandoc_manuscript.docx.postprocess.para_equation_style file.docx
Add --no-save to inspect the matching paragraph count without saving.
"""

from pathlib import Path
from typing import cast

from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Twips
from docx.styles.style import _ParagraphStyle
from docx.text.paragraph import Paragraph
from pydantic import Field
from pydantic_settings import BaseSettings, CliPositionalArg, SettingsConfigDict

from ..equation_layout import equation_tab_stops_from_page_width
from .common import get_body_text_style, open_docx, print_debug_success, print_error, print_info, save_docx


PARA_EQUATION_STYLE_NAME = "Para Equation"


def paragraph_contains_math(paragraph: Paragraph) -> bool:
    """Return whether a paragraph contains Word math elements."""
    element = paragraph._p
    return bool(element.findall(f".//{qn('m:oMath')}") or element.findall(f".//{qn('m:oMathPara')}"))


def paragraph_has_equation_layout(paragraph: Paragraph) -> bool:
    """Return whether a math paragraph uses Word tab stops for equation layout."""
    element = paragraph._p
    return bool(element.findall(f".//{qn('w:tab')}") or element.findall(f".//{qn('w:tabs')}"))


def is_tab_equation_paragraph(paragraph: Paragraph) -> bool:
    """Recognize tab-layout OMML or MathType equations using existing layout detection."""
    if not paragraph_has_equation_layout(paragraph) and paragraph.style.name != PARA_EQUATION_STYLE_NAME:
        return False
    if paragraph_contains_math(paragraph):
        return True
    # Standalone post-processing may receive MathType OLE instead of the build's OMML.
    for obj in paragraph._p.iter("{urn:schemas-microsoft-com:office:office}OLEObject"):
        prog_id = obj.get("ProgID", "")
        if prog_id.startswith("Equation.") or "MathType" in prog_id:
            return True
    return False


def configure_equation_tab_stops(doc: DocumentObject, style: _ParagraphStyle) -> None:
    """Set document-wide equation tabs from the first section's writable width."""
    section = doc.sections[0]
    dimensions = (section.page_width, section.left_margin, section.right_margin)
    if any(value is None for value in dimensions):
        raise ValueError("DOCX page width and left/right margins are required for equation tabs")
    center, right = equation_tab_stops_from_page_width(*(value.twips for value in dimensions))
    tabs = style.paragraph_format.tab_stops
    tabs.clear_all()
    # Word merges inherited tabs; cancel body-style stops that could intercept formula tabs.
    inherited_positions: set[int] = set()
    base = style.base_style
    visited: set[str] = set()
    while base is not None and base.style_id not in visited:
        visited.add(base.style_id)
        inherited_positions.update(tab.position.twips for tab in base.paragraph_format.tab_stops)
        base = base.base_style
    for position in sorted(inherited_positions - {center, right}):
        tabs.add_tab_stop(Twips(position), WD_TAB_ALIGNMENT.CLEAR)
    tabs.add_tab_stop(Twips(center), WD_TAB_ALIGNMENT.CENTER)
    tabs.add_tab_stop(Twips(right), WD_TAB_ALIGNMENT.RIGHT)
    print_debug_success(f"'Para Equation' tabs: center={center} twips, right={right} twips")


def process_para_equation_style(doc: DocumentObject) -> int:
    """Configure the equation style and apply it only to top-level tab equations."""
    paragraphs = [p for p in doc.paragraphs if is_tab_equation_paragraph(p)]
    if not paragraphs:
        return 0
    body_style, _ = get_body_text_style(doc)
    if body_style is None:
        raise ValueError("Neither 'Body Text' nor '正文文本' style was found")
    try:
        style = doc.styles[PARA_EQUATION_STYLE_NAME]
    except KeyError:
        style = doc.styles.add_style(PARA_EQUATION_STYLE_NAME, WD_STYLE_TYPE.PARAGRAPH)
    if style.type != WD_STYLE_TYPE.PARAGRAPH:
        raise ValueError("'Para Equation' must be a paragraph style")
    style = cast(_ParagraphStyle, style)
    style.base_style = body_style
    configure_equation_tab_stops(doc, style)
    # Word's paragraph text alignment controls vertical alignment within each line.
    p_pr = style.element.get_or_add_pPr()
    text_alignment = p_pr.find(qn("w:textAlignment"))
    if text_alignment is None:
        text_alignment = OxmlElement("w:textAlignment")
        p_pr.insert_element_before(
            text_alignment, "w:textboxTightWrap", "w:outlineLvl", "w:divId",
            "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
        )
    text_alignment.set(qn("w:val"), "center")
    style.paragraph_format.line_spacing = 1.0
    spacing = style.element.get_or_add_pPr().get_or_add_spacing()
    # Word stores line-based paragraph spacing in hundredths, not points/twips.
    spacing.attrib.pop(qn("w:after"), None)
    spacing.set(qn("w:afterLines"), "50")
    spacing.set(qn("w:afterAutospacing"), "0")
    style.hidden = False
    style.quick_style = True

    for paragraph in paragraphs:
        paragraph.style = style
        # Inherit vertical text alignment while preserving horizontal paragraph alignment.
        direct_alignment = paragraph._p.get_or_add_pPr().find(qn("w:textAlignment"))
        if direct_alignment is not None:
            direct_alignment.getparent().remove(direct_alignment)
        # Direct tabs merge with style tabs, so remove the old template's positions entirely.
        paragraph.paragraph_format.tab_stops.clear_all()
        direct_spacing = paragraph._p.get_or_add_pPr().find(qn("w:spacing"))
        if direct_spacing is not None:
            # Direct spacing would override the named style; retain before spacing.
            for attr in ("after", "afterLines", "afterAutospacing", "line", "lineRule"):
                direct_spacing.attrib.pop(qn(f"w:{attr}"), None)
            if not direct_spacing.attrib and len(direct_spacing) == 0:
                direct_spacing.getparent().remove(direct_spacing)
    print_debug_success(f"Applied 'Para Equation' style to {len(paragraphs)} paragraph(s)")
    return len(paragraphs)


class ParaEquationSettings(BaseSettings):
    """Options for processing one DOCX file in place."""

    model_config = SettingsConfigDict(cli_parse_args=True, cli_kebab_case=True, cli_implicit_flags=True)

    docx_path: CliPositionalArg[Path] = Field(default="manuscript.docx", description="DOCX file to process")  # Input and output file
    save: bool = Field(default=True, description="Save changes; --no-save inspects only")  # In-place save toggle


def main() -> int:
    """Apply equation formatting to a DOCX file and report failures to the CLI."""
    settings = ParaEquationSettings()
    try:
        doc, path = open_docx(settings.docx_path)
        if doc is None or path is None:
            return 1
        count = process_para_equation_style(doc)
        if settings.save:
            save_docx(doc, path)
        print_info(f"Matched {count} tab-layout equation paragraph(s); saved={settings.save}")
        return 0
    except Exception as exc:
        print_error(f"Para Equation processing failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
