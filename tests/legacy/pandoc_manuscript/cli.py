"""Dispatch Papper with only the selected command's settings loaded.

Pydantic still parses every argument. Ordinary invocations reserve all command
names but load selected settings; root help and unusual global-option ordering
use the complete model. Legacy settings/PmtCli imports remain lazy exports.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from importlib import import_module

from pydantic import Field, PrivateAttr, create_model
from pydantic_settings import BaseSettings, CliApp, CliSubCommand, SettingsConfigDict, get_subcommand

from . import __version__
from .commands.common import log
from .runtime.logging import verbose_logging
from .runtime.update_check import notify_and_schedule_update_check


_COMMANDS = {
    "init": ("commands.init", "InitSettings"),
    "setup": ("commands.setup", "SetupSettings"),
    "build": ("commands.build", "BuildCommandSettings"),
    "convert": ("commands.convert", "ConvertSettings"),
    "build_reply": ("commands.build_reply", "BuildReplySettings"),
    "clean": ("commands.clean", "CleanSettings"),
    "distclean": ("commands.clean", "DistcleanSettings"),
    "doctor": ("commands.doctor", "DoctorSettings"),
}
_EXPORTS = {name: module for module, name in _COMMANDS.values()}


class _DeferredCommand(BaseSettings):
    """Reserve unselected command names without importing their implementation."""


class _CliBase(BaseSettings):
    """Shared root options and dispatch for complete or selective CLI models."""

    model_config = SettingsConfigDict(
        cli_prog_name="papper",
        cli_kebab_case=True,
        cli_implicit_flags=True,
        cli_hide_none_type=True,
        cli_parse_none_str="auto",
        extra="ignore",
    )

    version_flag: bool = Field(default=False, alias="version")  # Print the installed version

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


def _load_settings(name: str) -> type[BaseSettings]:
    """Resolve and cache a legacy settings export on first actual use."""
    settings = globals().get(name)
    if settings is None:
        settings = getattr(import_module(f".{_EXPORTS[name]}", __package__), name)
        globals()[name] = settings
    return settings


@lru_cache(maxsize=10)
def _model_for_command(selected: str | None) -> type[_CliBase]:
    """Keep every command choice while loading selected settings or full help."""
    fields = {}
    for command, (_, name) in _COMMANDS.items():
        settings = _load_settings(name) if selected is None or selected == command else _DeferredCommand
        fields[command] = (CliSubCommand[settings | None], Field())  # Pydantic supplies unselected subcommands as None
    return create_model("PmtCli", __base__=_CliBase, __module__=__name__, __doc__="Papper command-line interface.", **fields)


def _get_cli_model(argv: list[str]) -> type[_CliBase]:
    """Choose which schema to load; leave all argument parsing to Pydantic."""
    if not argv or argv == ["--version"]:
        return _model_for_command("")
    for command in _COMMANDS:
        if argv[0] == command.replace("_", "-"):
            return _model_for_command(command)
    # Root help includes every command description. Global options before a
    # command retain the full parser rather than guessing their arity/order.
    return _model_for_command(None)


def __getattr__(name: str):
    """Preserve explicit imports of the complete CLI and settings classes."""
    if name in _EXPORTS:
        return _load_settings(name)
    if name == "PmtCli":
        return _model_for_command(None)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> int:
    """Run Papper with selected settings and the existing exit/update behavior."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    check_for_updates = True
    try:
        app = CliApp.run(_get_cli_model(arguments), cli_args=arguments, cli_parse_args=True)
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


__all__ = [*_EXPORTS, "PmtCli", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
