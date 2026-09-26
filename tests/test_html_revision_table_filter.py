from pathlib import Path
import shutil
import subprocess

import pytest


def test_html_revision_table_filter_marks_selected_rows_and_columns(tmp_path) -> None:
    """Apply the Revision Char style to the requested HTML table cells."""
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        pytest.skip("pandoc is not installed")

    repo_root = Path(__file__).resolve().parents[1]
    filter_path = repo_root / "pandoc" / "filters" / "html" / "revision_table_styles.lua"
    markdown = """\
| A | B | C |
|---|---|---|
| r1a | r1b | r1c |
| r2a | r2b | r2c |

: Rows {#tbl:rows revision_rows="2"}

| A | B | C |
|---|---|---|
| c1a | c1b | c1c |
| c2a | c2b | c2c |

: Columns {#tbl:columns revision_columns="2"}
"""

    result = subprocess.run(
        [pandoc, "--lua-filter", str(filter_path), "-f", "markdown", "-t", "html5"],
        input=markdown,
        text=True,
        capture_output=True,
        check=True,
    )

    html = result.stdout
    # Revision row indices include the header row, matching DOCX behavior.
    assert '<td data-custom-style="Revision Char">r1a</td>' in html
    assert '<td data-custom-style="Revision Char">r1b</td>' in html
    assert '<td data-custom-style="Revision Char">r1c</td>' in html
    assert '<td data-custom-style="Revision Char">c1b</td>' in html
    assert '<td data-custom-style="Revision Char">c2b</td>' in html
    assert '<td data-custom-style="Revision Char">r2a</td>' not in html
    assert '<td data-custom-style="Revision Char">c1a</td>' not in html
