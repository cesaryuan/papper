"""`papper setup` command implementation."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from ..common import VerboseCommandSettings, log
from .pandoc_tools import setup_pandoc_tools


class SetupSettings(VerboseCommandSettings):
    """Settings for `papper setup`."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    force: bool = Field(default=False, description="Redownload and reinstall managed Pandoc tools.")

    def run(self) -> int:
        """Download user-scoped Pandoc tools into ``~/.papper/tools``."""
        pandoc, crossref = setup_pandoc_tools(force=self.force)

        log(f"[OK] pandoc: {pandoc.executable} [{pandoc.source}]")
        log(f"[OK] pandoc-crossref: {crossref.executable} [{crossref.source}]")
        return 0
