from pathlib import Path
import shutil
import subprocess

import pytest


def _render_html(markdown: str, *extra_args: str) -> str:
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
            *extra_args,
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


def test_html_subfigure_filter_neutralizes_only_subfigure_layout_tables() -> None:
    """Expose the shared layout style to HTML CSS without styling ordinary tables."""
    from lxml import html as html_parser
    markdown = """\
<div id="fig:subfigure-example">
![Left panel.](a.png){#fig:subfigure-a width=49%}
![Right panel.](b.png){#fig:subfigure-b width=49%}

An example of a multi-subfigure layout.
</div>

| A | B |
|---|---|
| 1 | 2 |

: Regular table
"""

    html = _render_html(markdown, "--metadata", "subfigGrid=true")

    document = html_parser.fromstring(html)
    layouts = document.xpath('//figure[contains(@class, "subfigures")]//table')
    assert len(layouts) == 1
    assert layouts[0].get("data-custom-style") == "TableSubfigure"
    regular = document.xpath("//table[.//th='A']")[0]
    assert regular.get("data-custom-style") is None
    assert len(document.xpath("//table")) == 2


def test_html_paragraph_filter_styles_where_after_equations_and_body_after_tables() -> None:
    """Apply semantic custom styles to equation explanations and post-table body text."""
    markdown = """\
$$
x = 1
$$

where $x$ is a value.

A normal paragraph.

where this paragraph has no preceding equation.
"""

    html = _render_html(markdown)

    assert html.count('<div data-custom-style="Para Where">') == 1
    assert '<div data-custom-style="Para Where"' in html
    assert "<p>where this paragraph has no preceding equation.</p>" in html

    table_html = _render_html("""\
| A |
|---|
| 1 |

The paragraph immediately after the table.

A later paragraph.
""")

    assert table_html.count('<div data-custom-style="Para After Table">') == 1
    assert '<p>The paragraph immediately after the table.</p>' in table_html


def test_html_where_filter_recognizes_mathtype_tab_layout_equations() -> None:
    """Style explanations after MathType equations without treating tabs as tables."""
    markdown = """\
::: {#eq:mathtype}
`<w:r><w:tab /></w:r>`{=openxml} $x = 1$ `<w:r><w:tab /></w:r>`{=openxml} (1)
:::

where $x$ is a value.
"""

    html = _render_html(markdown)

    assert '<div data-custom-style="Para Where">' in html
    assert '<div data-custom-style="Para Where" data-where-layout="table">' not in html
