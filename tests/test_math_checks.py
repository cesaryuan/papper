from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.commands import build
from pandoc_manuscript.mathtype.preflight import find_hat_macro_order_issues


def test_find_hat_macro_order_issues_reports_locations_and_rewrites() -> None:
    """Report MathType-sensitive hat ordering with stable rewrite hints."""
    text = (
        "Safe: $\\mathbf{\\hat{C}}$.\n"
        "Warn: $\\hat{\\mathbf{C}}$ and $\\hat{\\mathcal{\\Gamma}}$.\n"
    )

    issues = find_hat_macro_order_issues(text)

    assert [(issue.line, issue.column) for issue in issues] == [(2, 8), (2, 31)]
    assert [issue.suggested_text for issue in issues] == [
        "\\mathbf{\\hat{C}}",
        "\\mathcal{\\hat{\\Gamma}}",
    ]


def test_find_hat_macro_order_issues_ignores_safe_and_non_style_forms() -> None:
    """Avoid warning on already-safe formulas or unrelated hat usage."""
    text = (
        "$\\hat{C}$\n"
        "$\\mathbf{\\hat{C}}$\n"
        "$\\hat{\\frac{a}{b}}$\n"
    )

    assert find_hat_macro_order_issues(text) == []


def test_run_build_command_applies_cli_mathtype_setting(tmp_path, monkeypatch) -> None:
    """Apply the one-build MathType choice to the settings passed to DOCX."""
    manuscript = tmp_path / "paper.md"
    manuscript.write_text("Body\n", encoding="utf-8")
    style = tmp_path / "style.yml"
    style.write_text("mathtype: false\n", encoding="utf-8")
    kwargs_seen: list[dict[str, object]] = []
    monkeypatch.setattr(build.SETTINGS, "style_file", str(style))
    monkeypatch.setattr(build, "build_docx", lambda **kwargs: kwargs_seen.append(kwargs))

    result = build.run_build_command(target="docx", markdown=str(manuscript), mathtype=True)

    assert result == 0
    assert kwargs_seen[0]["effective"].pmt_settings.mathtype is True
