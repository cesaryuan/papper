from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands.build_reply import resolve as reply_resolve
from pandoc_manuscript.docx.metadata import write_docx_pandoc_metadata
from pandoc_manuscript.runtime.metadata import EffectiveMetadata, PmtSettings


EQN_TEMPLATE = (
    '<w:pPr><w:tabs>'
    '<w:tab w:val="center" w:leader="none" w:pos="4888" />'
    '<w:tab w:val="right" w:leader="none" w:pos="9746" />'
    "</w:tabs></w:pPr>"
)


def test_build_writes_adjusted_docx_metadata_file(tmp_path, monkeypatch) -> None:
    """Pass Pandoc a generated metadata file with margin-synced equation tabs."""
    monkeypatch.chdir(tmp_path)

    effective = EffectiveMetadata(
        pmt_settings=PmtSettings.model_validate(
            {"docxPageMargins": {"left": "3.17cm", "right": "3.17cm"}}
        ),
        pandoc_metadata={"eqnBlockTemplate": EQN_TEMPLATE},
        has_yaml_header=True,
    )
    metadata_file = write_docx_pandoc_metadata(
        effective,
        use_mathtype=True,
    )

    metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
    assert metadata_file.is_relative_to(Path.home() / ".papper" / "projects")
    assert metadata_file.parent.name == "metadata"
    assert 'w:pos="4156"' in metadata["eqnBlockTemplate"]
    assert 'w:pos="8312"' in metadata["eqnBlockTemplate"]


def test_reply_labeled_equation_tabs_follow_metadata_margins() -> None:
    """Use margin-synced tab stops for resolved reply-side labeled equations."""
    markdown = "$$ a+b $$ {#eq:sum}"

    resolved = reply_resolve.replace_labeled_equation_blocks(
        markdown,
        {"eq:sum": "Equation 7"},
        {},
        PmtSettings.model_validate(
            {"docxPageMargins": {"left": "3.17cm", "right": "3.17cm"}}
        ),
    )

    assert 'w:pos="4156"' in resolved
    assert 'w:pos="8312"' in resolved
    assert "(7)" in resolved
