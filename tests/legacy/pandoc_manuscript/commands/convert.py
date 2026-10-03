"""Import one DOCX manuscript into a Markdown file and its referenced media.

This command runs Pandoc's DOCX reader with the MathType, equation-layout, and
cross-reference Lua filters in order. The filters live in the packaged Pandoc
resources, while the existing Rust library supplies MTEF decoding in process.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import CliPositionalArg, SettingsConfigDict

from ..runtime.logging import log_info, log_success
from ..runtime.resources import template_root
from ..mathtype.decode_docx import decode_documents
from .common import VerboseCommandSettings
from .setup import pandoc_tools_env, resolve_tool


FILTER_NAMES = ("mtef_parser.lua", "equation_tables.lua", "crossrefs.lua")


class ConvertSettings(VerboseCommandSettings):
    """Settings for `papper convert DOCX -o OUTPUT_DIR`."""

    model_config = SettingsConfigDict(
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_shortcuts={"output_dir": ["-o", "--output-dir"]},
    )

    docx: CliPositionalArg[Path] = Field(default=Path("manuscript.docx"), description="DOCX manuscript to import.")  # Source document
    output_dir: Path = Field(
        default=Path("converted"),
        validation_alias=AliasChoices("o", "output-dir"),
        description="Directory for Markdown and extracted media.",
    )  # Result directory

    def run(self) -> int:
        """Convert one DOCX with the bundled Pandoc filters."""
        source = self.docx.resolve()
        if not source.is_file() or source.suffix.lower() != ".docx":
            raise ValueError(f"Expected an existing DOCX file: {source}")
        output_dir = self.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        filters_dir = template_root() / "pandoc" / "filters" / "convert"
        filters = [filters_dir / name for name in FILTER_NAMES]
        for path in filters:
            if not path.is_file():
                raise FileNotFoundError(f"Convert filter is missing: {path}")

        pandoc = resolve_tool("pandoc").executable
        output = output_dir / f"{source.stem}.md"
        command = [str(pandoc), str(source), "--from=docx", "--to=markdown", "--wrap=none"]
        for path in filters:
            command.extend(("--lua-filter", str(path)))
        command.extend(("--extract-media=.", "--output", output.name))
        log_info(f"[convert] Importing {source}")
        decoded = decode_documents([source])
        # Share the in-process DLL results with Lua without another decoder process.
        with tempfile.TemporaryDirectory(prefix="papper-mtef-map-") as temp:
            mapping = Path(temp) / "equations.json"
            mapping.write_text(json.dumps(decoded, ensure_ascii=True), encoding="utf-8")
            environment = pandoc_tools_env({"MATHTYPE_LATEX_MAP": str(mapping)})
            subprocess.run(command, cwd=output_dir, env=environment, check=True)

        # Pandoc prefixes extracted paths with ./ when extracting into cwd.
        # Keep Markdown links source-relative for a portable manuscript folder.
        markdown = output.read_text(encoding="utf-8")
        normalized = markdown.replace("](./media/", "](media/")
        if normalized != markdown:
            output.write_text(normalized, encoding="utf-8", newline="\n")
        log_success(f"[convert] Wrote {output}")
        return 0
