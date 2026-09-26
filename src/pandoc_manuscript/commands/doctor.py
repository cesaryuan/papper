"""`papper doctor` command implementation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic_settings import SettingsConfigDict

from ..runtime.resources import template_root
from .setup import ensure_pandoc_tools, pandoc_tools_env, resolve_tool
from .common import VerboseCommandSettings, log


def command_status(command: list[str]) -> tuple[bool, str]:
    """Run a short diagnostic command and return whether it succeeded."""
    try:
        if command[0] in {"pandoc", "pandoc-crossref"}:
            pandoc, crossref = ensure_pandoc_tools()
            tool = pandoc if command[0] == "pandoc" else crossref
        else:
            tool = resolve_tool(command[0])
        result = subprocess.run(
            [str(tool.executable), *command[1:]],
            capture_output=True,
            text=True,
            check=False,
            env=pandoc_tools_env(),
        )
    except (OSError, RuntimeError) as exc:
        return False, str(exc)
    first_line = (result.stdout or result.stderr).splitlines()
    detail = first_line[0] if first_line else f"exit code {result.returncode}"
    return result.returncode == 0, f"{detail} [{tool.source}: {tool.executable}]"


def import_status(module_name: str) -> tuple[bool, str]:
    """Return whether a Python dependency can be imported."""
    try:
        __import__(module_name)
    except Exception as exc:
        return False, str(exc)
    return True, "available"


class DoctorSettings(VerboseCommandSettings):
    """Settings for `papper doctor`."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    project_dir: Path = Path(".")

    def run(self) -> int:
        """Check the local environment and current manuscript project."""
        project_dir = self.project_dir.resolve()
        root = template_root()
        checks: list[tuple[str, bool, str]] = []

        for command in (["pandoc", "--version"], ["pandoc-crossref", "--version"]):
            ok, detail = command_status(command)
            checks.append((" ".join(command), ok, detail))

        for module_name in ("docx", "yaml", "lxml", "panflute", "resvg_py", "pymupdf"):
            ok, detail = import_status(module_name)
            checks.append((f"python import {module_name}", ok, detail))

        checks.extend(
            [
                ("papper package build module", True, "pandoc_manuscript.commands.build"),
                ("papper pandoc defaults", (root / "pandoc" / "pandoc-docx.yml").exists(), str(root / "pandoc")),
                (
                    "papper HTML defaults",
                    (root / "pandoc" / "pandoc-html.yml").exists(),
                    str(root / "pandoc" / "pandoc-html.yml"),
                ),
                (
                    "papper DOCX metadata filter",
                    (root / "pandoc" / "filters" / "docx" / "docx_metadata.lua").exists(),
                    str(root / "pandoc" / "filters" / "docx" / "docx_metadata.lua"),
                ),
                (
                    "papper shared AST filters",
                    all(
                        (root / "pandoc" / "filters" / name).exists()
                        for name in (
                            "shared/normalize_chinese_numbering.lua",
                            "shared/merge_table_cells.lua",
                            "shared/paragraph_custom_styles.lua",
                        )
                    ),
                    str(root / "pandoc" / "filters"),
                ),
                (
                    "papper HTML revision filter",
                    (root / "pandoc" / "filters" / "html" / "revision_table_styles.lua").exists(),
                    str(root / "pandoc" / "filters" / "html" / "revision_table_styles.lua"),
                ),
                (
                    "papper HTML subfigure filter",
                    (root / "pandoc" / "filters" / "html" / "subfigure_layout_styles.lua").exists(),
                    str(root / "pandoc" / "filters" / "html" / "subfigure_layout_styles.lua"),
                ),
                (
                    "papper DOCX AST filters",
                    (root / "pandoc" / "filters" / "docx" / "inline_math_spacing.lua").exists(),
                    str(root / "pandoc" / "filters" / "docx"),
                ),
                ("project directory", project_dir.exists(), str(project_dir)),
                ("project manuscript.md", (project_dir / "manuscript.md").exists(), str(project_dir / "manuscript.md")),
                ("project style.yml", (project_dir / "style.yml").exists(), str(project_dir / "style.yml")),
            ]
        )

        has_error = False
        for label, ok, detail in checks:
            prefix = "[OK]" if ok else "[ERROR]"
            log(f"{prefix} {label}: {detail}")
            has_error = has_error or not ok
        return 1 if has_error else 0
