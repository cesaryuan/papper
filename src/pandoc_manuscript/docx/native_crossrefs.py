"""Bind Lua-exported heading records to Word's native multilevel numbering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from ..runtime.logging import log_debug


HEADING_MARKER = "PMT_NATIVE_HEADING:"


class NativeHeadingNumbering:
    """Keep heading records and numbering definitions together during finalization."""

    def __init__(self, path: Path) -> None:
        """Open the generated manuscript without changing its existing list definitions."""
        self.path = path
        self.document = Document(str(path))
        self.records: list[tuple[Any, dict[str, Any]]] = []
        self.levels: dict[int, dict[str, Any]] = {}
        self.heading_styles: dict[int, str] = {}

    @staticmethod
    def element(tag: str, **attributes: object) -> Any:
        """Create one WordprocessingML element with namespace-qualified attributes."""
        node = OxmlElement(f"w:{tag}")
        for key, value in attributes.items():
            node.set(qn(f"w:{key}"), str(value))
        return node

    def collect(self) -> None:
        """Consume hidden records while preserving formatted heading title runs."""
        for paragraph in self.document.element.body.iter(qn("w:p")):
            for run in list(paragraph.findall(qn("w:r"))):
                text = "".join(node.text or "" for node in run.iter(qn("w:t")))
                if not text.startswith(HEADING_MARKER):
                    continue
                record = json.loads(text[len(HEADING_MARKER):])
                level = int(record["level"])
                if not 1 <= level <= 9:
                    raise ValueError(f"Native DOCX heading level must be between 1 and 9: {level}")
                previous = self.levels.get(level)
                if previous and previous["pattern"] != record["pattern"]:
                    raise ValueError(f"Native DOCX heading level {level} has inconsistent numbering templates")
                self.levels.setdefault(level, record)
                self.records.append((paragraph, record))
                paragraph.remove(run)

    def create_numbering(self) -> int:
        """Append a separate outline list and link its levels to the heading styles."""
        numbering = self.document.part.numbering_part.element
        abstract_id = max(
            (int(node.get(qn("w:abstractNumId"))) for node in numbering.findall(qn("w:abstractNum"))),
            default=-1,
        ) + 1
        num_id = max(
            (int(node.get(qn("w:numId"))) for node in numbering.findall(qn("w:num"))),
            default=0,
        ) + 1
        abstract = self.element("abstractNum", abstractNumId=abstract_id)
        abstract.append(self.element("multiLevelType", val="multilevel"))
        abstract.append(self.element("name", val="Papper native headings"))
        depth = max(int(record["depth"]) for _, record in self.records)
        for level in range(1, 10):
            record = self.levels.get(level)
            style = self.document.styles[f"Heading {level}"]
            self.heading_styles[level] = style.style_id
            definition = self.element("lvl", ilvl=level - 1)
            definition.append(self.element("start", val=record["start"] if record else 1))
            definition.append(self.element("numFmt", val="decimal"))
            # Omission restarts after the preceding level. Word can discard linked
            # heading levels when that default is written as an explicit lvlRestart.
            if level <= depth:
                definition.append(self.element("pStyle", val=style.style_id))
            definition.append(self.element("suff", val=record["suffix"] if record else "space"))
            pattern = record["pattern"] if record else ".".join(f"%{index}" for index in range(1, level + 1))
            definition.append(self.element("lvlText", val=pattern))
            definition.append(self.element("lvlJc", val="left"))
            abstract.append(definition)
            if level <= depth:
                num_pr = style.element.get_or_add_pPr().get_or_add_numPr()
                num_pr.get_or_add_ilvl().val = level - 1
                num_pr.get_or_add_numId().val = num_id
        # The schema requires all abstract definitions before concrete num entries.
        first_num = numbering.find(qn("w:num"))
        if first_num is None:
            numbering.append(abstract)
        else:
            first_num.addprevious(abstract)
        instance = self.element("num", numId=num_id)
        instance.append(self.element("abstractNumId", val=abstract_id))
        numbering.append(instance)
        return num_id

    def bind_paragraphs(self, num_id: int) -> None:
        """Number marked headings and explicitly suppress numbering on other headings."""
        marked = {paragraph: record for paragraph, record in self.records}
        style_levels = {style_id: level for level, style_id in self.heading_styles.items()}
        for paragraph in self.document.element.body.iter(qn("w:p")):
            record = marked.get(paragraph)
            p_pr = paragraph.find(qn("w:pPr"))
            p_style = p_pr.find(qn("w:pStyle")) if p_pr is not None else None
            style_id = p_style.get(qn("w:val")) if p_style is not None else None
            if record is None and style_id not in style_levels:
                continue
            p_pr = paragraph.get_or_add_pPr()
            num_pr = p_pr.get_or_add_numPr()
            num_pr.get_or_add_numId().val = num_id if record else 0
            if record:
                num_pr.get_or_add_ilvl().val = int(record["level"]) - 1
                # Custom heading styles still need a real outline level for Word.
                outline = p_pr.find(qn("w:outlineLvl"))
                if outline is None:
                    p_pr.append(self.element("outlineLvl", val=int(record["level"]) - 1))
                else:
                    outline.set(qn("w:val"), str(int(record["level"]) - 1))

    def finalize(self) -> int:
        """Write native numbering only when the Lua filter exported numbered headings."""
        self.collect()
        if not self.records:
            return 0
        self.bind_paragraphs(self.create_numbering())
        self.document.save(str(self.path))
        return len(self.records)


def finalize_native_heading_numbering(path: Path) -> None:
    """Complete native heading output independently of optional DOCX formatting."""
    count = NativeHeadingNumbering(path).finalize()
    log_debug(f"[DOCX] Bound {count} heading(s) to native multilevel numbering")
