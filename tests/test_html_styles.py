from pathlib import Path

import pytest

from pandoc_manuscript.html.styles import build_reference_style_css
from pandoc_manuscript.runtime.metadata import PmtSettings


@pytest.mark.parametrize("style_name", ["Heading 1", "标题 1"])
def test_html_docx_style_aliases_match_localized_heading_names(tmp_path: Path, style_name: str) -> None:
    """Apply one heading override when the configured name uses either locale."""
    styles_xml = tmp_path / "styles.xml"
    styles_xml.write_text(
        """<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="标题 1"/>
    <w:rPr><w:sz w:val="30"/></w:rPr>
  </w:style>
</w:styles>""",
        encoding="utf-8",
    )
    settings = PmtSettings.model_validate({"docxStyle": {style_name: {"fontSize": "四号"}}})

    css = build_reference_style_css(styles_xml, settings)

    assert "h1 {" in css
    assert "font-size: 14pt;" in css
    assert f"docxStyle.{style_name} override" in css
