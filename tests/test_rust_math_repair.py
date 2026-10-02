"""Verify native Word-import math repair preserves Markdown and safely repairs in place."""

from pathlib import Path

from test_rust_cli_contract import rust_executable
from test_rust_other_commands import _native


def test_native_math_repair_matches_import_contract(tmp_path: Path, rust_executable: Path) -> None:
    """Restore three math delimiters while retaining links, escapes, whitespace and TeX commands outside math."""
    source = tmp_path / "imported.md"
    text = (r"Link [a\_b](media/a\_b.png), normal \{text\}, and \alpha." + "\n\n"
            + r"\$ x\_1 + \frac\{a\}\{b\} \$" + "\n"
            + r"\( \alpha\^2 + 3\% \)" + "\n"
            + "\\[ a\\&b\n  + c \\]" + "\n")
    expected = (r"Link [a\_b](media/a\_b.png), normal \{text\}, and \alpha." + "\n\n"
                + r"$x_1 + \frac{a}{b}$" + "\n"
                + r"\(\alpha^2 + 3%\)" + "\n"
                + r"\[a&b + c\]" + "\n")
    source.write_text(text, encoding="utf-8")
    printed = _native(rust_executable, ["repair-math", str(source)], tmp_path, tmp_path / "home")
    assert printed.returncode == 0, printed.stderr
    assert printed.stdout == expected
    assert source.read_text(encoding="utf-8") == text
    repaired = _native(rust_executable, ["repair-math", str(source), "-o", str(source)], tmp_path, tmp_path / "home")
    assert repaired.returncode == 0, repaired.stderr
    assert source.read_text(encoding="utf-8") == expected


def test_native_atomic_output_supports_deep_unicode_paths(tmp_path: Path, rust_executable: Path) -> None:
    """Publish complete repaired output beyond Windows MAX_PATH without touching its Unicode source."""
    source = tmp_path / "原始稿件.md"
    source.write_text(r"Before \$x\_1\$ after", encoding="utf-8")
    destination = tmp_path.joinpath(*(["nested-manuscript-resources"] * 12), "公式输出.md")
    result = _native(rust_executable, ["repair-math", str(source), "-o", str(destination)], tmp_path, tmp_path / "home")
    assert result.returncode == 0, result.stderr
    assert destination.read_text(encoding="utf-8") == "Before $x_1$ after"
    assert source.read_text(encoding="utf-8") == r"Before \$x\_1\$ after"
