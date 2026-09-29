"""`papper init` command implementation."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    CliPositionalArg,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from ..runtime.resources import iter_project_template_entries, project_template_root
from .setup import setup_pandoc_tools
from .common import VerboseCommandSettings, log, project_directory


IGNORE_NAMES = {
    ".git",
    ".papper",
    ".pmt",
    ".pandoc-cache",
    ".venv",
    "__pycache__",
    "output",
    "tmp",
    "target",
}

AGENTS_TEMPLATE_DESTINATION = "AGENTS.md"
AGENTS_DIRECTORY_DESTINATION = ".agents"
# Keep the legacy markers so existing generated AGENTS.md blocks remain recognizable.
AGENTS_TEMPLATE_START = "<!-- pmt template guidance: begin -->"
AGENTS_TEMPLATE_END = "<!-- pmt template guidance: end -->"


def copy_template_entry(source: Path, destination: Path, *, overwrite: bool) -> None:
    """Copy one template file or directory while avoiding generated artifacts."""
    if destination.exists():
        if not overwrite:
            raise FileExistsError(f"Target already exists: {destination}")
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    if source.is_dir():
        shutil.copytree(source, destination, ignore=ignore_generated_artifacts)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def ignore_generated_artifacts(directory: str, names: list[str]) -> set[str]:
    """Return generated/cache names that should not be copied into new projects."""
    return {name for name in names if name in IGNORE_NAMES or name.endswith(".pyc")}


def merge_agents_template(source: Path, destination: Path) -> None:
    """Append the packaged AGENTS guidance to an existing AGENTS.md file."""
    source_text = source.read_text(encoding="utf-8").strip()
    existing_text = destination.read_text(encoding="utf-8")
    if AGENTS_TEMPLATE_START in existing_text:
        log(f"[OK] AGENTS.md already contains the packaged guidance: {destination}")
        return

    merged_text = (
        existing_text.rstrip()
        + "\n\n"
        + AGENTS_TEMPLATE_START
        + "\n\n"
        + source_text
        + "\n\n"
        + AGENTS_TEMPLATE_END
        + "\n"
    )
    destination.write_text(merged_text, encoding="utf-8", newline="\n")
    log(f"[OK] Merged AGENTS.md: {destination}")


def merge_template_directory(source: Path, destination: Path) -> tuple[int, int]:
    """Copy missing files from a packaged template directory into an existing project."""
    if destination.exists() and not destination.is_dir():
        raise FileExistsError(f"Target exists and is not a directory: {destination}")

    copied = 0
    skipped = 0
    destination.mkdir(parents=True, exist_ok=True)
    for source_child in source.rglob("*"):
        relative_path = source_child.relative_to(source)
        if any(part in IGNORE_NAMES or part.endswith(".pyc") for part in relative_path.parts):
            continue

        destination_child = destination / relative_path
        if source_child.is_dir():
            destination_child.mkdir(parents=True, exist_ok=True)
            continue

        if destination_child.exists():
            skipped += 1
            continue

        destination_child.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_child, destination_child)
        copied += 1

    log(f"[OK] Merged {source.name}: copied {copied}, kept existing {skipped}: {destination}")
    return copied, skipped


class InitSettings(VerboseCommandSettings):
    """Settings for `papper init`."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    directory: CliPositionalArg[str] = Field(
        default=".",
        description="Target manuscript project directory. Defaults to the current directory.",
    )
    force: bool = Field(default=False, description="Overwrite existing template entries in the target project.")
    merge: bool = Field(
        default=False,
        description="Merge packaged agent guidance into existing AGENTS.md and .agents entries.",
    )
    setup: bool = Field(default=False, description="Download user-scoped Pandoc tools after init.")
    lang: str | None = Field(
        default=None,
        description="Template language. Use zh-cn for Chinese manuscript and reviewer-reply templates.",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use parsed CLI values only so ambient LANG does not select templates."""
        return (init_settings,)

    def normalized_lang(self) -> str | None:
        """Return the supported normalized init language or reject an unknown value."""
        if self.lang is None:
            return None
        normalized = self.lang.strip().replace("_", "-").casefold()
        if normalized != "zh-cn":
            raise ValueError("Only `--lang zh-cn` is currently supported by `papper init`.")
        return normalized

    def run(self) -> int:
        """Create a new manuscript project from the packaged template files."""
        root = project_template_root()
        target = Path(self.directory).resolve()
        lang = self.normalized_lang()

        if self.force and self.merge:
            raise RuntimeError("Use only one of --force or --merge for `papper init`.")

        target.mkdir(parents=True, exist_ok=True)
        template_entries = list(iter_project_template_entries(lang))
        existing_entries = {
            destination_name
            for _, destination_name in template_entries
            if (target / destination_name).exists()
        }
        blocking_entries = sorted(name for name in existing_entries if name != "AGENTS.md")
        if blocking_entries and not self.force and not self.merge:
            joined = ", ".join(blocking_entries)
            raise RuntimeError(
                f"Target already contains template files: {joined}. "
                f"Use --force to overwrite them: {target}"
            )
        if "AGENTS.md" in existing_entries and not self.force and not self.merge:
            log("[WARN] AGENTS.md already exists; use --merge to combine the packaged template notes automatically.")

        for source_name, destination_name in template_entries:
            source = root / source_name
            if not source.exists():
                continue
            destination = target / destination_name
            if destination_name == AGENTS_TEMPLATE_DESTINATION and destination.exists():
                if self.merge:
                    merge_agents_template(source, destination)
                elif not self.force:
                    continue
                else:
                    copy_template_entry(source, destination, overwrite=True)
                continue
            if destination_name == AGENTS_DIRECTORY_DESTINATION and destination.exists():
                if self.merge:
                    merge_template_directory(source, destination)
                elif not self.force:
                    continue
                else:
                    copy_template_entry(source, destination, overwrite=True)
                continue
            if destination.exists() and not self.force:
                continue
            copy_template_entry(source, destination, overwrite=self.force)

        # Empty directories are not preserved in wheels/sdists, but manuscripts
        # conventionally keep figures under images/ from the beginning.
        (target / "images").mkdir(exist_ok=True)

        if lang == "zh-cn":
            log("[INFO] Selected Chinese manuscript and reviewer-reply templates.")
        log(f"[OK] Created manuscript project: {target}")
        if self.setup:
            with project_directory(target):
                pandoc, crossref = setup_pandoc_tools()
            log(f"[OK] pandoc: {pandoc.executable} [{pandoc.source}]")
            log(f"[OK] pandoc-crossref: {crossref.executable} [{crossref.source}]")
        log("Next: cd into the project and run `papper doctor`, then `papper build docx`.")
        return 0
