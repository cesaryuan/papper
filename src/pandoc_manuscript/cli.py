"""CLI entry point for Papper and its legacy command aliases."""

from __future__ import annotations

import sys

from pydantic import Field, PrivateAttr
from pydantic_settings import BaseSettings, CliApp, CliSubCommand, SettingsConfigDict, get_subcommand

from . import __version__
from .commands.build import BuildCommandSettings
from .commands.clean import CleanSettings, DistcleanSettings
from .commands.convert import ConvertSettings
from .commands.common import log
from .commands.doctor import DoctorSettings
from .commands.init import InitSettings
from .commands.build_reply import BuildReplySettings
from .commands.setup import SetupSettings
from .runtime.logging import verbose_logging
from .runtime.update_check import notify_and_schedule_update_check


class PmtCli(BaseSettings):
    """Papper command-line interface."""

    model_config = SettingsConfigDict(
        cli_prog_name="papper",
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_hide_none_type=True,
        cli_parse_none_str="auto",
        extra="ignore",
    )

    version_flag: bool = Field(default=False, alias="version")
    init: CliSubCommand[InitSettings | None]
    setup: CliSubCommand[SetupSettings | None]
    build: CliSubCommand[BuildCommandSettings | None]
    convert: CliSubCommand[ConvertSettings | None]
    build_reply: CliSubCommand[BuildReplySettings | None]
    clean: CliSubCommand[CleanSettings | None]
    distclean: CliSubCommand[DistcleanSettings | None]
    doctor: CliSubCommand[DoctorSettings | None]

    _exit_code: int = PrivateAttr(default=0)

    def cli_cmd(self) -> None:
        """Dispatch the selected Papper subcommand."""
        if self.version_flag:
            log(f"papper {__version__}")
            self._exit_code = 0
            return
        command = get_subcommand(self, is_required=False)
        if command is None:
            log("Use `papper --help` to see available commands.")
            self._exit_code = 1
            return
        with verbose_logging(command.verbose):
            self._exit_code = int(command.run())


def main(argv: list[str] | None = None) -> int:
    """Run the Papper command-line interface."""
    check_for_updates = True
    try:
        app = CliApp.run(PmtCli, cli_args=argv, cli_parse_args=True)
        return app._exit_code
    except KeyboardInterrupt:
        # Do not turn an immediate Ctrl-C into a network wait.
        check_for_updates = False
        print("\n[WARN] Interrupted by user.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    finally:
        if check_for_updates:
            notify_and_schedule_update_check(__version__)


__all__ = [
    "BuildCommandSettings",
    "BuildReplySettings",
    "CleanSettings",
    "ConvertSettings",
    "DistcleanSettings",
    "DoctorSettings",
    "InitSettings",
    "PmtCli",
    "SetupSettings",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
