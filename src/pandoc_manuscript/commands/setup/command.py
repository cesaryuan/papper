"""`papper setup` command implementation."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from ..common import VerboseCommandSettings, log
from .pandoc_tools import setup_pandoc_tools


class SetupSettings(VerboseCommandSettings):
    """Settings for `papper setup`."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    force: bool = Field(default=False, description="Refresh legacy tool downloads when no native engine is available.")  # Bundled engines are validated locally

    def run(self) -> int:
        """Validate the bundled engine or prepare tools for an unbuilt source checkout."""
        pandoc, crossref = setup_pandoc_tools(force=self.force)

        log(f"[OK] pandoc: {pandoc.executable} [{pandoc.source}]")
        log(f"[OK] pandoc-crossref: {crossref.executable} [{crossref.source}]")
        return 0
