from pathlib import Path
import shutil
import subprocess

import pytest
from lxml import html as html_parser


def _render_html(markdown: str) -> str:
    """Render Markdown through the repository HTML defaults and filters."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            pandoc,
            "--defaults",
            str(repo_root / "pandoc" / "pandoc-html.yml"),
            "-f",
            "markdown",
            "-t",
            "html5",
        ],
        input=markdown,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_html_where_filter_recognizes_mathtype_tab_layout_equations() -> None:
    """Style explanations after MathType equations without treating tabs as tables."""
    markdown = """\
::: {#eq:mathtype}
`<w:r><w:tab /></w:r>`{=openxml} $x = 1$ `<w:r><w:tab /></w:r>`{=openxml} (1)
:::

where $x$ is a value.
"""

    html = _render_html(markdown)

    # This tab-layout input is absent from snapshots; check semantics across writer wrapping.
    paragraphs = html_parser.fromstring(html).xpath('//div[@data-custom-style="Para Where"]')
    assert len(paragraphs) == 1
    assert paragraphs[0].get("data-where-layout") is None
