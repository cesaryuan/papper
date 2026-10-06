"""Protect meaningful content differences when presentation source is normalized."""

import pytest
from lxml import html

from snapshot_utils import semantic_html


def test_semantic_snapshot_allows_equivalent_presentation_sources() -> None:
    """CSS restructuring and serialization changes must not invalidate document content."""
    before = '<html><head><style>p {color:red}</style></head><body><p class="old" style="color:red" id="intro">See <a href="#intro">this section</a>.</p></body></html>'
    after = '<html><head><style>/* extracted rule */ .new {color:red}</style></head><body><p id="intro" class="new">See <a href="#intro">this section</a>.</p></body></html>'
    assert semantic_html(before) == semantic_html(after)


@pytest.mark.parametrize("changed", [
    '<p id="intro">See <a href="#missing">this section</a>.</p>',
    '<p id="intro">See <a href="#intro">another section</a>.</p>',
    '<p id="missing">See <a href="#intro">this section</a>.</p>',
])
def test_semantic_snapshot_keeps_text_and_reference_relationships(changed: str) -> None:
    """Normalization must not hide lost text, broken links, or removed reference targets."""
    original = '<p id="intro">See <a href="#intro">this section</a>.</p>'
    assert semantic_html(original) != semantic_html(changed)


def test_semantic_snapshot_preserves_significant_spaces_and_code() -> None:
    """Inline spaces, NBSPs and preformatted line breaks carry user-visible meaning."""
    source = '<p>A <em>B</em> C\u00a0D</p><pre><code>  x\n    y</code></pre>'
    result = html.document_fromstring(semantic_html(source))
    assert result.xpath("//p")[0].text_content() == "A B C\u00a0D"
    assert result.xpath("//code")[0].text_content() == "  x\n    y"
    assert semantic_html(source) != semantic_html(source.replace("A <em>", "A<em>"))
