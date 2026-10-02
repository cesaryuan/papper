"""
Pandoc filter to replace bold math commands with \\mathbfit in math equations.

This filter processes all Math elements (both inline and display math) and
replaces various LaTeX bold math commands with \\mathbfit for consistent
rendering in DOCX output.

Replaced commands:
- \\boldsymbol → \\mathbfit
- \\bm → \\mathbfit
- \\symbf → \\mathbfit
- \\mathbold → \\mathbfit
- \\pmb → \\mathbfit
- \\mathbfup → \\mathbfit

Usage:
    pandoc input.md --filter pandoc/filters/docx/to_mathbfit.py -o output.docx
"""

import re
from typing import Union
import panflute as pf


# List of bold math commands to replace (in order of preference for replacement)
BOLD_MATH_COMMANDS = [
    '\\boldsymbol{',
    '\\bm{',
    '\\symbf{',
    '\\mathbold{',
    '\\pmb{',
    '\\mathbfup{',
]


def replace_bold_commands(math_text: str) -> str:
    """
    Replace all bold math commands with \\mathbfit in LaTeX math.

    Args:
        math_text: The original LaTeX math string

    Returns:
        The math string with bold commands replaced by \\mathbfit
    """
    result = math_text

    # Replace each bold command with \mathbfit
    for command in BOLD_MATH_COMMANDS:
        result = result.replace(command, '\\mathbfit{')
        # print(command)  # Debugging output to trace changes

    return result


def action(elem: pf.Element, doc: pf.Doc) -> Union[pf.Element, None]:
    """
    Panflute action function to process each element.

    Args:
        elem: The element to process
        doc: The document

    Returns:
        Modified element or None
    """
    # Process both inline and display math
    if isinstance(elem, pf.Math):
        # Replace bold math commands with \mathbfit in the math text
        elem.text = replace_bold_commands(elem.text)
        return elem

    return None


def main(doc: pf.Doc | None = None):
    """
    Main function for the filter.

    Args:
        doc: The document (provided by panflute)

    Returns:
        Processed document
    """
    return pf.run_filter(action, doc=doc)


if __name__ == '__main__':
    main()
