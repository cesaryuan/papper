from pathlib import Path
import shutil
import subprocess
import zipfile
import xml.etree.ElementTree as ET

import pytest


def paragraph_style_ids(document_xml: str) -> list[str]:
    """Return paragraph style ids from a DOCX document XML payload."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ET.fromstring(document_xml)
    style_ids: list[str] = []
    for paragraph in root.findall(".//w:p", ns):
        style = paragraph.find("./w:pPr/w:pStyle", ns)
        if style is not None:
            value = style.get(f"{{{ns['w']}}}val")
            if value is not None:
                style_ids.append(value)
    return style_ids


def test_captionless_image_filter_ignores_inline_images(tmp_path) -> None:
    """Do not style inline image paragraphs as Figure."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    repo_root = Path(__file__).resolve().parents[1]
    filter_path = repo_root / "pandoc" / "filters" / "docx" / "captionless_image_style.lua"
    reference_doc = repo_root / "pandoc" / "manuscript-template" / "reference-doc.docx"
    image_path = repo_root / "template" / "examples" / "images" / "single-figure-example.png"
    output_path = tmp_path / "inline-image.docx"

    subprocess.run(
        [
            pandoc,
            "--lua-filter",
            str(filter_path),
            "--reference-doc",
            str(reference_doc),
            "-f",
            "markdown",
            "-o",
            str(output_path),
        ],
        input=f"Inline ![]({image_path.as_posix()}){{width=10%}} image.\n",
        text=True,
        capture_output=True,
        check=True,
    )

    with zipfile.ZipFile(output_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "Figure" not in paragraph_style_ids(document_xml)
