#!/usr/bin/env python3
"""Restore over-escaped LaTeX math in Markdown converted from Word.

This script only touches math spans and blocks. It intentionally avoids
global unescaping so that links, images, tables, and normal Markdown text
remain unchanged.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


INLINE_DOLLAR_RE = re.compile(r"\\\$(.+?)\\\$", re.DOTALL)
PAREN_MATH_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
BRACKET_MATH_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def unescape_math_content(content: str) -> str:
    replacements = [
        (r"\\\\", r"\\"),
        (r"\{", "{"),
        (r"\}", "}"),
        (r"\_", "_"),
        (r"\^", "^"),
        (r"\%", "%"),
        (r"\#", "#"),
        (r"\&", "&"),
        (r"\,", r"\,"),
        (r"\;", r"\;"),
        (r"\!", r"\!"),
    ]

    fixed = content
    for source, target in replacements:
        fixed = fixed.replace(source, target)

    fixed = re.sub(r"\\([A-Za-z]+)", r"\\\1", fixed)
    fixed = re.sub(r"\s+", " ", fixed).strip()
    return fixed


def restore_dollar_math(text: str) -> str:
    return INLINE_DOLLAR_RE.sub(lambda m: f"${unescape_math_content(m.group(1))}$", text)


def restore_paren_math(text: str) -> str:
    return PAREN_MATH_RE.sub(lambda m: f"\\({unescape_math_content(m.group(1))}\\)", text)


def restore_bracket_math(text: str) -> str:
    return BRACKET_MATH_RE.sub(lambda m: f"\\[{unescape_math_content(m.group(1))}\\]", text)


def fix_markdown_math(text: str) -> str:
    fixed = restore_dollar_math(text)
    fixed = restore_paren_math(fixed)
    fixed = restore_bracket_math(fixed)
    return fixed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input Markdown file")
    parser.add_argument("output", nargs="?", type=Path, help="Optional output Markdown file")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    source = args.input.read_text(encoding="utf-8")
    fixed = fix_markdown_math(source)

    if args.output:
        args.output.write_text(fixed, encoding="utf-8")
    else:
        print(fixed, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
