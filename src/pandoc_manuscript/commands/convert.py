"""Import one DOCX manuscript into a Markdown file and its referenced media.

This command runs Pandoc's DOCX reader with the MathType, equation-layout, and
cross-reference Lua filters in order. The filters live in the packaged Pandoc
resources, while the Rust executable supplies MTEF decoding when required.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

from pydantic import AliasChoices, Field
from pydantic_settings import CliPositionalArg, SettingsConfigDict

from ..runtime.logging import log_info, log_success
from ..runtime.resources import package_resource_path, source_tree_root, template_root
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
        converter_env = {"MATHTYPE_RUST_EXE": str(resolve_converter())} if has_mathtype_ole(source) else {}
        environment = pandoc_tools_env(converter_env)
        log_info(f"[convert] Importing {source}")
        subprocess.run(command, cwd=output_dir, env=environment, check=True)

        # Pandoc prefixes extracted paths with ./ when extracting into cwd.
        # Keep Markdown links source-relative for a portable manuscript folder.
        markdown = output.read_text(encoding="utf-8")
        normalized = markdown.replace("](./media/", "](media/")
        if normalized != markdown:
            output.write_text(normalized, encoding="utf-8", newline="\n")
        log_success(f"[convert] Wrote {output}")
        return 0


def resolve_converter() -> Path:
    """Find the wheel executable or build the source checkout's Rust CLI."""
    executable = "mathtype-rust.exe" if os.name == "nt" else "mathtype-rust"
    source_root = source_tree_root()
    if source_root is not None:
        project = source_root / "scripts" / "mathtype-rust"
        target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or project / "target")
        if not target_dir.is_absolute():
            target_dir = Path.cwd() / target_dir
        candidates = (target_dir / "debug" / executable, target_dir / "release" / executable)
        manifest = project / "Cargo.toml"
        source_files = [manifest, project / "Cargo.lock", *project.joinpath("src").rglob("*.rs")]
        for candidate in candidates:
            if candidate.is_file() and all(
                not path.is_file() or path.stat().st_mtime <= candidate.stat().st_mtime for path in source_files
            ):
                return candidate.resolve()
        log_info("[convert] Building the MathType converter with Cargo")
        subprocess.run(["cargo", "build", "--locked", "--manifest-path", str(manifest), "--bin", "mathtype-rust"], check=True)
        if candidates[0].is_file():
            return candidates[0].resolve()
    packaged = package_resource_path(Path("mathtype/bin") / executable)
    if packaged.is_file():
        return packaged.resolve()
    raise FileNotFoundError(
        "MathType converter executable is missing; build scripts/mathtype-rust with Cargo or install a Papper wheel that includes it"
    )


def has_mathtype_ole(docx: Path) -> bool:
    """Require the Rust executable only for DOCX parts that declare MathType OLE."""
    with ZipFile(docx) as archive:
        for part in archive.namelist():
            if part.startswith("word/") and part.endswith(".xml") and "/_rels/" not in part:
                content = archive.read(part)
                if b"OLEObject" in content and (b"Equation." in content or b"MathType" in content):
                    return True
    return False
