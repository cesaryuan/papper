"""Apply optional automatic page numbers to DOCX footers."""

from __future__ import annotations

import re
from typing import Any, Iterator

from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.section import _Footer
from docx.text.paragraph import Paragraph

from ...runtime.metadata import PmtSettings


PAGE_FIELD_PATTERN = re.compile(r"\bPAGE\b", re.IGNORECASE)
PAGE_NUMBER_STYLE_NAME = "page number"


def is_page_field_instruction(instruction: str | None) -> bool:
    """Return whether a Word field instruction renders a page number."""
    return bool(instruction and PAGE_FIELD_PATTERN.search(instruction))


def iter_distinct_footers(doc: DocumentObject) -> Iterator[_Footer]:
    """Yield each explicitly defined footer part once across all sections."""
    seen: set[str] = set()
    for section in doc.sections:
        for footer in (section.footer, section.first_page_footer, section.even_page_footer):
            # Linked footers inherit the preceding part, which is processed when its
            # owning section is visited. Avoid creating redundant footer parts here.
            if footer.is_linked_to_previous:
                continue
            part_name = str(footer.part.partname)
            if part_name not in seen:
                seen.add(part_name)
                yield footer


def iter_footer_paragraphs(footer: _Footer) -> Iterator[Paragraph]:
    """Yield footer paragraphs, including paragraphs nested in footer tables."""
    containers: list[Any] = [footer]
    seen_paragraphs: set[int] = set()
    while containers:
        container = containers.pop()
        for paragraph in container.paragraphs:
            paragraph_id = id(paragraph._p)
            if paragraph_id not in seen_paragraphs:
                seen_paragraphs.add(paragraph_id)
                yield paragraph
        for table in container.tables:
            for row in table.rows:
                containers.extend(row.cells)


def paragraph_has_page_field(paragraph: Paragraph) -> bool:
    """Return whether a footer paragraph contains a simple or complex PAGE field."""
    for field in paragraph._p.iter(qn("w:fldSimple")):
        if is_page_field_instruction(field.get(qn("w:instr"))):
            return True
    return any(
        is_page_field_instruction(instruction.text)
        for instruction in paragraph._p.iter(qn("w:instrText"))
    )


def footer_has_page_field(footer: _Footer) -> bool:
    """Return whether any footer paragraph has a PAGE field."""
    return any(paragraph_has_page_field(paragraph) for paragraph in iter_footer_paragraphs(footer))


def page_number_style_id(paragraph: Paragraph) -> str:
    """Return the reference-DOCX character style ID reserved for page numbers."""
    try:
        return paragraph.part.get_style_id(PAGE_NUMBER_STYLE_NAME, WD_STYLE_TYPE.CHARACTER)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"DOCX reference document must define the `{PAGE_NUMBER_STYLE_NAME}` character style"
        ) from exc


def apply_page_number_style(run, style_id: str) -> None:
    """Apply the page-number character style to one PAGE field run."""
    run_properties = OxmlElement("w:rPr")
    run_style = OxmlElement("w:rStyle")
    run_style.set(qn("w:val"), style_id)
    run_properties.append(run_style)
    run.insert(0, run_properties)


def append_page_field(paragraph: Paragraph) -> None:
    """Append a styled Word PAGE field whose cached value is visible before refresh."""
    style_id = page_number_style_id(paragraph)
    for field_type, text in (("begin", None), (None, " PAGE "), ("separate", None), (None, "1"), ("end", None)):
        run = OxmlElement("w:r")
        apply_page_number_style(run, style_id)
        if field_type is None:
            child = OxmlElement("w:instrText" if text == " PAGE " else "w:t")
            child.text = text
            if text == " PAGE ":
                child.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        else:
            child = OxmlElement("w:fldChar")
            child.set(qn("w:fldCharType"), field_type)
        run.append(child)
        paragraph._p.append(run)


def add_page_numbers(doc: DocumentObject) -> tuple[int, int]:
    """Add one PAGE field using the page number style to each defined footer."""
    updated = 0
    existing = 0
    footers = list(iter_distinct_footers(doc))
    if not footers and doc.sections:
        # A new python-docx document has no footer part until one is unlinked.
        # Materialize the primary footer so a true setting can create page numbers.
        doc.sections[0].footer.is_linked_to_previous = False
        footers = list(iter_distinct_footers(doc))
    for footer in footers:
        if footer_has_page_field(footer):
            existing += 1
            continue
        paragraph = footer.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        append_page_field(paragraph)
        updated += 1
    return updated, existing


def remove_page_fields(paragraph: Paragraph) -> int:
    """Remove PAGE fields from one paragraph while preserving surrounding footer text."""
    removed = 0
    for field in list(paragraph._p.iter(qn("w:fldSimple"))):
        if is_page_field_instruction(field.get(qn("w:instr"))):
            field.getparent().remove(field)
            removed += 1

    children = list(paragraph._p)
    open_fields: list[tuple[int, list[str]]] = []
    ranges: list[tuple[int, int]] = []
    for index, child in enumerate(children):
        for field_char in child.iter(qn("w:fldChar")):
            field_type = field_char.get(qn("w:fldCharType"))
            if field_type == "begin":
                open_fields.append((index, []))
            elif field_type == "end" and open_fields:
                start, instructions = open_fields.pop()
                if is_page_field_instruction("".join(instructions)):
                    ranges.append((start, index))
        for instruction in child.iter(qn("w:instrText")):
            for _, instructions in open_fields:
                instructions.append(instruction.text or "")

    for start, end in reversed(ranges):
        for child in children[start : end + 1]:
            if child.getparent() is paragraph._p:
                paragraph._p.remove(child)
        removed += 1
    return removed


def remove_page_numbers(doc: DocumentObject) -> int:
    """Remove PAGE fields from every explicitly defined footer in a document."""
    return sum(
        remove_page_fields(paragraph)
        for footer in iter_distinct_footers(doc)
        for paragraph in iter_footer_paragraphs(footer)
    )


def apply_page_number_settings(doc: DocumentObject, settings: PmtSettings) -> dict[str, Any] | None:
    """Apply docxShowPageNumbers when explicitly configured in Papper metadata."""
    visible = settings.docx_show_page_numbers
    if visible is None:
        return None
    if visible:
        added, existing = add_page_numbers(doc)
        return {"visible": True, "added": added, "existing": existing}
    return {"visible": False, "removed": remove_page_numbers(doc)}
