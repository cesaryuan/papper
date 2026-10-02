"""Insert author names, affiliations, and corresponding-author text into HTML."""

from __future__ import annotations

from lxml import etree


def normalize_author_metadata(metadata: dict) -> tuple[list[dict], dict[str, str]]:
    """Normalize inline or keyed affiliations to stable letter keys."""
    authors = metadata.get("authors", metadata.get("author", [])) or []
    raw_affiliations = metadata.get("affiliations", metadata.get("affiliation", {})) or {}
    affiliations: dict[str, str] = {}
    key_by_text: dict[str, str] = {}

    def index_to_alpha(index: int) -> str:
        """Convert a 1-based index to a, ..., z, aa, ... labels."""
        label = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            label = chr(ord("a") + remainder) + label
        return label

    def register(value: object) -> str:
        """Register an inline affiliation and return its generated key."""
        text = str(value).strip()
        if not text:
            return ""
        if text in key_by_text:
            return key_by_text[text]
        key = index_to_alpha(len(affiliations) + 1)
        affiliations[key] = text
        key_by_text[text] = key
        return key

    if isinstance(raw_affiliations, dict):
        for key, value in raw_affiliations.items():
            text = str(value).strip()
            if text:
                affiliations[str(key)] = text
                key_by_text[text] = str(key)

    normalized_authors: list[dict] = []
    for raw_author in authors:
        if not isinstance(raw_author, dict):
            continue
        author = dict(raw_author)
        raw_keys = author.get("affiliations", author.get("affiliation", [])) or []
        if isinstance(raw_keys, str):
            raw_keys = [raw_keys]
        keys: list[str] = []
        for value in raw_keys:
            text = str(value).strip()
            if not text:
                continue
            keys.append(text if text in affiliations else key_by_text.get(text, register(text)))
        author["affiliations"] = keys
        normalized_authors.append(author)
    return normalized_authors, affiliations


def _author_footnote(author: dict, affiliations: dict[str, str]) -> str:
    """Build the corresponding-author sentence used below the author block."""
    corresponding = author.get("corresponding", False)
    if isinstance(corresponding, str):
        return corresponding.strip()
    if not corresponding:
        return ""
    text = f"Correspondence to: Dr. {author.get('name', '')}".strip()
    if author.get("title"):
        text += f" ({author['title']})"
    keys = author.get("affiliations", []) or []
    if keys and affiliations.get(keys[0]):
        text += f", {affiliations[keys[0]]}"
    if author.get("email"):
        text += f". E-mail: {author['email']}."
    return text


def _insert_after(anchor: etree._Element, element: etree._Element) -> None:
    """Insert an element immediately after an existing HTML element."""
    anchor.addnext(element)


def insert_author_info(document: etree._ElementTree, metadata: dict) -> tuple[int, int, bool]:
    """Insert a Papper author block after the document title."""
    authors, affiliations = normalize_author_metadata(metadata)
    if not authors:
        return 0, 0, False

    root = document.getroot()
    body = root.find(".//body")
    if body is None:
        return 0, 0, False

    for existing in body.xpath(".//*[contains(concat(' ', normalize-space(@class), ' '), ' papper-authors ')]"):
        existing.getparent().remove(existing)

    title = body.xpath(".//h1[contains(concat(' ', normalize-space(@class), ' '), ' title ')]")
    author_block = etree.Element("div", {"class": "papper-authors"})
    author_line = etree.SubElement(author_block, "p", {"class": "author"})
    for index, author in enumerate(authors):
        if index:
            previous = author_line[-1] if len(author_line) else None
            if previous is not None:
                previous.tail = ", "
            elif author_line.text is None:
                author_line.text = ", "
        name = etree.SubElement(author_line, "span", {"class": "author-name"})
        name.text = str(author.get("name", ""))
        keys = author.get("affiliations", []) or []
        if keys:
            sup = etree.SubElement(author_line, "sup")
            sup.text = ",".join(keys)
        if author.get("corresponding"):
            sup = etree.SubElement(author_line, "sup")
            sup.text = "*"

    for key in sorted(affiliations, key=lambda value: (not str(value).isdigit(), str(value))):
        line = etree.SubElement(author_block, "p", {"class": "affiliation"})
        sup = etree.SubElement(line, "sup")
        sup.text = str(key)
        sup.tail = f" {affiliations[key]}"

    footnotes = [text for text in (_author_footnote(author, affiliations) for author in authors) if text]
    if footnotes:
        line = etree.SubElement(author_block, "p", {"class": "corresponding-author"})
        marker = etree.SubElement(line, "sup")
        marker.text = "*"
        marker.tail = footnotes[0]

    if title:
        _insert_after(title[0], author_block)
    else:
        body.insert(0, author_block)
    return len(authors), len(affiliations), bool(footnotes)
