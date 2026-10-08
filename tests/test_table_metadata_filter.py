from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


def test_table_metadata_filter_keeps_uncaptioned_table_attributes(tmp_path) -> None:
    """Keep metadata for tables that intentionally omit visible captions."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    filter_path = Path(__file__).resolve().parents[1] / "pandoc" / "filters" / "docx" / "docx_metadata.lua"
    output_path = tmp_path / "table-metadata.docx"
    markdown = """\
| **Algorithm: Demo** |
|---|
| Step |
: {revision_rows="*"}

| Text style |
|---|
| Body text |
: {custom-text-style="Body Text"}
"""

    subprocess.run(
        [pandoc, "--lua-filter", str(filter_path), "-f", "markdown", "-o", str(output_path)],
        input=markdown,
        text=True,
        capture_output=True,
        check=True,
    )

    with zipfile.ZipFile(output_path) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")

    assert "PMT_TABLE_METADATA:" in document_xml
    assert '"revision_rows":"*"' in document_xml
    assert '"custom_text_style":"Body Text"' in document_xml
