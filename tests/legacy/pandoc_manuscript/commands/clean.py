"""`papper clean` and `papper distclean` command implementations."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict

from ..runtime.logging import log_error, log_info, log_success, log_warning
from ..runtime.paths import project_state_dir, project_work_dir
from .build import DEFAULT_OUTPUT_DIR
from .common import VerboseCommandSettings


def is_relative_to(path: Path, parent: Path) -> bool:
    """Return True when path is inside parent on Python versions without Path.is_relative_to needs."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def ensure_safe_clean_dir(output_dir: Path) -> None:
    """Reject unsafe recursive clean targets caused by a custom output directory."""
    project_dir = Path.cwd().resolve()
    resolved_output = output_dir.resolve()

    # Custom output dirs are useful, but deleting the project root or parent
    # directory is too destructive for a convenience clean command.
    if resolved_output == project_dir or not is_relative_to(resolved_output, project_dir):
        raise ValueError(f"Refusing to clean unsafe output directory: {output_dir}")


def clean(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> None:
    """Remove generated outputs and transient work files, keeping reusable caches."""
    log_info("\n[Clean] Cleaning generated files...\n")

    output_path = Path(output_dir)
    if output_path.exists():
        ensure_safe_clean_dir(output_path)
        shutil.rmtree(output_path)
        log_info(f"Removed: {output_path}")

    work_dir = project_work_dir()
    if work_dir.exists():
        shutil.rmtree(work_dir)
        log_info(f"Removed: {work_dir}")

    log_success("\n[OK] Clean complete.")


def distclean(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> None:
    """Deep clean generated outputs, transient work files, and reusable caches."""
    log_info("\n[Clean] Deep cleaning...\n")
    clean(output_dir)

    for cache_dir in (project_state_dir(), Path(".pandoc-cache")):
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            log_info(f"Removed: {cache_dir}")

    log_success("\n[OK] Deep clean complete.")


class CleanSettings(VerboseCommandSettings):
    """Settings for `papper clean` and `papper distclean`."""

    model_config = SettingsConfigDict(
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_hide_none_type=True,
        cli_parse_none_str="auto",
        cli_shortcuts={"output_dir": ["-o", "--output-dir"]},
        populate_by_name=True,
    )
    target: ClassVar[Literal["clean", "distclean"]] = "clean"

    output_dir: str = Field(
        default=DEFAULT_OUTPUT_DIR,
        validation_alias=AliasChoices("o", "output-dir"),
        description="Base output directory.",
    )
    def run(self) -> int:
        """Run the clean target."""
        try:
            if self.target == "distclean":
                distclean(self.output_dir)
            else:
                clean(self.output_dir)
            return 0
        except KeyboardInterrupt:
            log_warning("\n\n[WARN] Clean interrupted by user.")
            return 1
        except Exception as exc:
            log_error(f"\n[ERROR] {exc}")
            return 1


class DistcleanSettings(CleanSettings):
    """Settings for `papper distclean`."""

    target: ClassVar[Literal["clean", "distclean"]] = "distclean"
