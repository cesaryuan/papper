#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-docx>=1.1.0",
# ]
# ///
"""
Validate that the final DOCX does not contain unrendered Pandoc syntax.

This checker scans the generated OOXML text for common Pandoc constructs that
should have been compiled away, such as fenced div markers, cross-reference
labels, and attribute blocks. The build uses it as a final guard so broken
filters or malformed source syntax fail fast instead of shipping a visibly
incorrect DOCX.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from .common import print_debug, print_error, print_info, print_success, print_warning


WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
DOCX_TEXT_PART_PATTERN = re.compile(r"^word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$")
UNRENDERED_PANDOC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Pandoc fenced_divs marker",
        # Pandoc fenced_divs markers such as "::: {.note}" or closing ":::" lines
        # should be consumed before the final DOCX is shipped.
        re.compile(r"^\s*:{3,}(?:\s*\{[^{}]*\}|\s*)$"),
    ),
    (
        "Pandoc attribute block",
        # Attribute blocks such as {#fig:demo width=50%} should not survive in final DOCX text.
        re.compile(r"\{#[A-Za-z][\w.-]*(?::[\w.-]+)?(?:\s+[^{}]+)?\}"),
    ),
    (
        "Pandoc cross-reference label",
        # Cross-reference ids like @sec:intro or #fig:overview indicate failed Pandoc processing.
        re.compile(r"(?<![\w/])[@#](?:sec|fig|tbl|eq|app|alg|lem|thm|cor|def|prop|exm|exr):[A-Za-z0-9._:-]+"),
    ),
    (
        "Pandoc citation syntax",
        # Citation brackets such as [@doe2024] should have been rendered into the target citation style.
        re.compile(r"\[\s*@[\w:-]+(?:\s*;\s*@[\w:-]+)*\s*\]"),
    ),
    (
        "Pandoc bare citation syntax",
        # Bare citations such as @doe2024 or -@doe2024 should also disappear in
        # final DOCX text. Exclude known cross-reference prefixes so @sec:intro
        # continues to be reported as a cross-reference issue instead.
        re.compile(
            r"(?<![\w./-])-?@"
            r"(?!(?:sec|fig|tbl|eq|app|alg|lem|thm|cor|def|prop|exm|exr):)"
            r"[A-Za-z0-9_][A-Za-z0-9_:+-]*"
        ),
    ),
    (
        "raw HTML tag",
        # Raw HTML such as <div>, </span>, <img ...>, <br/>, or <!-- ... --> should
        # not appear as literal text in the final DOCX unless Pandoc failed to parse it.
        re.compile(
            r"<!--.*?-->|</?[A-Za-z][A-Za-z0-9:-]*(?:\s+[^<>]*?)?\s*/?>"
        ),
    ),
    (
        "duplicated reference label word",
        # Broken cross-reference rendering can duplicate the visible label text,
        # leaving artifacts such as "Section Section" or "Figure Figure".
        re.compile(r"\b(Section|Figure|Table|Equation)\s+\1\b"),
    ),
    (
        "duplicated parenthesized reference label",
        # A manual label before an already labeled reference can produce
        # "Equation (Equation 6)" instead of "Equation (6)".
        re.compile(
            r"\b(Section|Figure|Table|Equation)\s*\(\s*\1\s+"
            r"\d+(?:\.\d+)*\s*\)"
        ),
    ),
    (
        "duplicated numbered reference label",
        # Some failures duplicate the full numbered prefix, such as
        # "Table 3 Table 3" or "Figure 2.1 Figure 2.1".
        re.compile(
            r"\b(Section|Figure|Table|Equation)\s+"
            r"(\d+(?:\.\d+)*)\s+\1\s+\2\b"
        ),
    ),
)


@dataclass(frozen=True)
class SyntaxFinding:
    """Describe one suspicious text fragment found in a DOCX text part."""

    part_name: str
    paragraph_index: int
    pattern_name: str
    matched_text: str
    paragraph_text: str


def normalize_text(text: str) -> str:
    """Collapse paragraph whitespace so logs stay readable and stable."""
    return re.sub(r"\s+", " ", text).strip()


def collect_paragraph_text(paragraph_element: ET.Element) -> str:
    """Join all visible text runs inside a paragraph-like OOXML element."""
    parts = [node.text or "" for node in paragraph_element.findall(".//w:t", WORD_NAMESPACE)]
    return normalize_text("".join(parts))


def iter_text_paragraphs(docx_path: str | Path) -> Iterable[tuple[str, int, str]]:
    """Yield visible paragraph text from the main DOCX document and side parts."""
    with zipfile.ZipFile(docx_path, "r") as package:
        part_names = sorted(
            name for name in package.namelist() if DOCX_TEXT_PART_PATTERN.match(name)
        )
        for part_name in part_names:
            root = ET.fromstring(package.read(part_name))
            paragraph_index = 0
            for paragraph in root.findall(".//w:p", WORD_NAMESPACE):
                text = collect_paragraph_text(paragraph)
                if not text:
                    continue
                paragraph_index += 1
                yield part_name, paragraph_index, text


def detect_unrendered_pandoc_syntax(text: str) -> list[tuple[str, str]]:
    """Return matched pattern labels and fragments for one paragraph of text."""
    findings: list[tuple[str, str]] = []
    for pattern_name, pattern in UNRENDERED_PANDOC_PATTERNS:
        for match in pattern.finditer(text):
            findings.append((pattern_name, match.group(0)))
    return findings


def summarize_part_name(part_name: str) -> str:
    """Map OOXML part names to compact human-readable log labels."""
    if part_name == "word/document.xml":
        return "document"
    if part_name.startswith("word/header"):
        return part_name.replace("word/", "").replace(".xml", "")
    if part_name.startswith("word/footer"):
        return part_name.replace("word/", "").replace(".xml", "")
    if part_name == "word/footnotes.xml":
        return "footnotes"
    if part_name == "word/endnotes.xml":
        return "endnotes"
    return part_name


def find_unrendered_pandoc_syntax(
    docx_path: str | Path,
    max_findings: int = 20,
) -> list[SyntaxFinding]:
    """Scan a DOCX file for unrendered Pandoc syntax and return suspicious matches."""
    findings: list[SyntaxFinding] = []
    for part_name, paragraph_index, text in iter_text_paragraphs(docx_path):
        paragraph_findings = detect_unrendered_pandoc_syntax(text)
        for pattern_name, matched_text in paragraph_findings:
            findings.append(
                SyntaxFinding(
                    part_name=part_name,
                    paragraph_index=paragraph_index,
                    pattern_name=pattern_name,
                    matched_text=matched_text,
                    paragraph_text=text,
                )
            )
            if len(findings) >= max_findings:
                return findings
    return findings


def validate_final_docx_syntax(docx_path: str | Path, max_findings: int = 20) -> list[SyntaxFinding]:
    """Log the final DOCX syntax check and return any suspicious findings."""
    docx_file = Path(docx_path).resolve()
    print_debug(f"Running final DOCX syntax check in: {docx_file}")

    findings = find_unrendered_pandoc_syntax(docx_file, max_findings=max_findings)
    if not findings:
        print_success("Final DOCX syntax check passed")
        return []

    print_error("Final DOCX syntax check failed")
    print_warning(
        "Found text that still looks like raw Pandoc syntax. "
        "This usually means a source block/filter/cross-reference/raw HTML fragment was not rendered, "
        "or a visible reference label was duplicated unexpectedly."
    )
    for finding in findings:
        part_label = summarize_part_name(finding.part_name)
        print_error(
            f"[{part_label} paragraph {finding.paragraph_index}] "
            f"{finding.pattern_name}: {finding.matched_text}"
        )
        print_info(f"  Context: {finding.paragraph_text}")
    return findings


def main() -> None:
    """Run the final DOCX syntax checker from the command line."""
    parser = argparse.ArgumentParser(
        description="Check a DOCX for visible unrendered Pandoc syntax."
    )
    parser.add_argument("docx_path", help="Path to the DOCX file to validate")
    parser.add_argument(
        "--max-findings",
        type=int,
        default=20,
        help="Maximum number of suspicious findings to report before stopping",
    )
    args = parser.parse_args()

    findings = validate_final_docx_syntax(args.docx_path, max_findings=args.max_findings)
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
