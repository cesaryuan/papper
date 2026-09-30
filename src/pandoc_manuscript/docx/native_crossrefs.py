"""Finalize native headings and randomize bookmark IDs before DOCX formatting."""

from __future__ import annotations

import json
from pathlib import Path
from secrets import randbelow
from typing import Any

from docx import Document
from docx.opc.part import XmlPart
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from lxml import etree

from ..runtime.logging import log_debug


HEADING_MARKER = "PMT_NATIVE_HEADING:"


class NativeCrossrefs:
    """Keep heading records and document bookmark identities together during finalization."""

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

    def randomize_bookmark_ids(self) -> int:
        """Give all bookmark pairs distinct random IDs across the document's XML parts."""
        roots = []
        tags = {qn("w:bookmarkStart"), qn("w:bookmarkEnd")}
        for part in self.document.part.package.parts:
            if not str(part.partname).startswith("/word/") or not str(part.partname).endswith(".xml"):
                continue
            root = part.element if isinstance(part, XmlPart) else parse_xml(part.blob)
            if any(node.tag in tags for node in root.iter()):
                roots.append((part, root))
        used = {node.get(qn("w:id")) for _, root in roots for node in root.iter() if node.tag in tags}
        count = 0
        for part, root in roots:
            replacements = {}
            for node in root.iter(qn("w:bookmarkStart")):
                old_id = node.get(qn("w:id"))
                if old_id in replacements:
                    continue
                # Word accepts signed 32-bit IDs; reserve each draw before pairing ends.
                new_id = str(randbelow(2**31 - 1) + 1)
                while new_id in used:
                    new_id = str(randbelow(2**31 - 1) + 1)
                used.add(new_id)
                replacements[old_id] = new_id
            for node in root.iter():
                if node.tag in tags and node.get(qn("w:id")) in replacements:
                    node.set(qn("w:id"), replacements[node.get(qn("w:id"))])
            if not isinstance(part, XmlPart):
                # python-docx loads footnotes/comments as generic OPC parts.
                part._blob = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
            count += len(replacements)
        return count

    def finalize(self) -> tuple[int, int]:
        """Write bookmark identities even when the manuscript has no numbered headings."""
        self.collect()
        if self.records:
            self.bind_paragraphs(self.create_numbering())
        bookmark_count = self.randomize_bookmark_ids()
        if self.records or bookmark_count:
            self.document.save(str(self.path))
        return len(self.records), bookmark_count


def finalize_native_crossrefs(path: Path) -> None:
    """Complete native headings and bookmark IDs independently of optional formatting."""
    heading_count, bookmark_count = NativeCrossrefs(path).finalize()
    log_debug(f"[DOCX] Bound {heading_count} heading(s) to native multilevel numbering")
    log_debug(f"[DOCX] Randomized {bookmark_count} bookmark ID(s)")
