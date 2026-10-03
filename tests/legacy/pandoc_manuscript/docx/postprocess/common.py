"""Shared helpers for DOCX post-processing scripts."""

from pathlib import Path
from typing import Iterable, cast

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.styles.style import _ParagraphStyle
from docx.table import Table
from docx.text.paragraph import Paragraph

from ...runtime.logging import log_message


BODY_TEXT_STYLE_NAMES = ("Body Text", "正文文本")
NORMAL_STYLE_NAMES = ("Normal", "正文")


class Colors:
    """ANSI color codes for terminal output."""

    GREEN = "\033[92m"
    CYAN = "\033[96m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"


def print_success(message: str) -> None:
    """Print a user-facing success message in green."""
    log_message("SUCCESS", f"{Colors.GREEN}{message}{Colors.RESET}")


def print_info(message: str) -> None:
    """Print a user-facing informational message in cyan."""
    log_message("INFO", f"{Colors.CYAN}{message}{Colors.RESET}")


def print_debug(message: str) -> None:
    """Print detailed post-processing progress when DEBUG logging is enabled."""
    log_message("DEBUG", f"{Colors.CYAN}{message}{Colors.RESET}")


def print_debug_success(message: str) -> None:
    """Print detailed post-processing success output when DEBUG logging is enabled."""
    log_message("DEBUG", f"{Colors.GREEN}{message}{Colors.RESET}")


def print_warning(message: str) -> None:
    """Print a warning message in yellow."""
    log_message("WARNING", f"{Colors.YELLOW}{message}{Colors.RESET}")


def print_error(message: str) -> None:
    """Print an error message in red."""
    log_message("ERROR", f"{Colors.RED}{message}{Colors.RESET}")


def validate_existing_file(path: str | Path, label: str) -> Path | None:
    """Return a resolved existing file path, or log a consistent validation error."""
    candidate = Path(path)
    if not candidate.exists():
        print_error(f"{label} not found: {path}")
        return None
    return candidate.resolve()


def open_docx(docx_path: str | Path) -> tuple[DocumentObject | None, Path | None]:
    """Validate and open a DOCX file for standalone post-process scripts."""
    print_debug("Validating inputs...")
    docx_file = validate_existing_file(docx_path, "DOCX file")
    if docx_file is None:
        return None, None

    print_debug(f"Processing: {docx_file}")
    print_debug("Opening document...")
    return Document(str(docx_file)), docx_file


def save_docx(doc: DocumentObject, docx_path: str | Path) -> None:
    """Save a DOCX document with consistent progress logging."""
    print_debug("Saving document...")
    doc.save(str(docx_path))
    print_debug_success("Document saved")


def get_or_add_child(parent, tag: str):
    """Return an existing child element or append a new one with the given OOXML tag."""
    child = parent.find(qn(tag))
    if child is None:
        child = OxmlElement(tag)
        parent.append(child)
    return child


def get_or_add_tbl_pr(table: Table):
    """Return a table properties element, creating one for tables missing it."""
    tbl = table._element
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    return tbl_pr


def get_first_existing_paragraph_style(
    doc: DocumentObject, style_names: tuple[str, ...]
) -> tuple[_ParagraphStyle | None, str | None]:
    """Return the first existing paragraph style from a candidate list."""
    for style_name in style_names:
        try:
            return cast(_ParagraphStyle, doc.styles[style_name]), style_name
        except KeyError:
            continue
    return None, None


def get_body_text_style(doc: DocumentObject) -> tuple[_ParagraphStyle | None, str | None]:
    """Return the document Body Text/正文文本 paragraph style, if available."""
    return get_first_existing_paragraph_style(doc, BODY_TEXT_STYLE_NAMES)


def get_normal_style(doc: DocumentObject) -> tuple[_ParagraphStyle | None, str | None]:
    """Return the document Normal/正文 paragraph style, if available."""
    return get_first_existing_paragraph_style(doc, NORMAL_STYLE_NAMES)


def iter_body_blocks(doc: DocumentObject) -> Iterable[Paragraph | Table]:
    """Yield top-level body paragraphs and tables in document order."""
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def set_style_first_line_indent_chars(
    style: _ParagraphStyle, chars: float, field_name: str = "first-line indent"
) -> None:
    """Set a paragraph style first-line indent using Word character-based OOXML."""
    if chars < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")

    p_pr = style.element.get_or_add_pPr()
    ind = p_pr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        p_pr.append(ind)

    # Word stores character indents in hundredths of a character; remove twip attrs to avoid conflicts.
    ind.set(qn("w:firstLineChars"), str(int(round(chars * 100))))
    for attr_name in ("w:firstLine", "w:hanging", "w:hangingChars"):
        attr = qn(attr_name)
        if attr in ind.attrib:
            del ind.attrib[attr]
