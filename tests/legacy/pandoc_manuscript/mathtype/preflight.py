"""Pre-build math syntax checks shared by manuscript and reply builds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from ..runtime.logging import log_warning


# MathType-exported PDFs can drop the hat when a style macro is nested inside
# ``\hat{...}``, so warn on the conservative ``\hat{\macro{x}}`` pattern.
HAT_MACRO_ORDER_PATTERN = re.compile(
    r"""
    \\hat
    \s*\{
        \s*(?P<macro>\\[A-Za-z]+)
        \s*\{
            \s*(?P<symbol>\\[A-Za-z]+|[A-Za-z0-9])
            \s*\}
        \s*\}
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class HatMacroOrderIssue:
    """One MathType-sensitive `\\hat{\\macro{x}}` occurrence."""

    line: int
    column: int
    matched_text: str
    suggested_text: str


def line_and_column(text: str, offset: int) -> tuple[int, int]:
    """Return 1-based line and column numbers for a match offset."""
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset)
    column = offset + 1 if line_start < 0 else offset - line_start
    return line, column


def normalize_formula_snippet(text: str) -> str:
    """Collapse match whitespace so warnings stay readable on one line."""
    return re.sub(r"\s+", " ", text).strip()


def suggested_hat_macro_order(macro: str, symbol: str) -> str:
    """Return the MathType-safe rewrite for one flagged formula fragment."""
    return f"{macro}{{\\hat{{{symbol}}}}}"


def find_hat_macro_order_issues(text: str) -> list[HatMacroOrderIssue]:
    """Find `\\hat{\\macro{x}}` fragments that should be rewritten for MathType."""
    issues: list[HatMacroOrderIssue] = []
    for match in HAT_MACRO_ORDER_PATTERN.finditer(text):
        line, column = line_and_column(text, match.start())
        issues.append(
            HatMacroOrderIssue(
                line=line,
                column=column,
                matched_text=match.group(0),
                suggested_text=suggested_hat_macro_order(
                    match.group("macro"),
                    match.group("symbol"),
                ),
            )
        )
    return issues


def warn_mathtype_hat_style_order(path: Path) -> list[HatMacroOrderIssue]:
    """Warn when MathType-sensitive hat ordering appears in one Markdown file."""
    issues = find_hat_macro_order_issues(path.read_text(encoding="utf-8"))
    if not issues:
        return []

    log_warning(
        f"[WARN] MathType hat-order check found {len(issues)} formula(s) in {path} "
        "that may lose the hat in exported PDFs:"
    )
    for issue in issues:
        log_warning(
            f"[WARN]   {path}:{issue.line}:{issue.column} "
            f"`{normalize_formula_snippet(issue.matched_text)}` -> "
            f"`{issue.suggested_text}`"
        )
    log_warning(
        "[WARN] Rewrite these as `\\something{\\hat{C}}` style before relying on MathType PDF output."
    )
    return issues
