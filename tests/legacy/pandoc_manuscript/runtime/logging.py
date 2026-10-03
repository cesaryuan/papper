"""Small shared logging gate for repository scripts."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

from tqdm import tqdm


LOG_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
}
DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV = "PANDOC_TEMPLATE_LOG_LEVEL"
ANSI_RESET = "\033[0m"
LOG_LEVEL_COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[96m",
    "SUCCESS": "\033[92m",
    "WARNING": "\033[93m",
    "WARN": "\033[93m",
    "ERROR": "\033[91m",
}
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
WINDOWS_COLOR_SUPPORT: dict[int, bool] = {}


def normalize_log_level(level: str | None) -> int:
    """Return a numeric log level, falling back to WARNING for invalid values."""
    if level is None:
        return LOG_LEVELS[DEFAULT_LOG_LEVEL]
    return LOG_LEVELS.get(level.strip().upper(), LOG_LEVELS[DEFAULT_LOG_LEVEL])


def current_log_level() -> int:
    """Return the active minimum log level from the environment."""
    return normalize_log_level(os.environ.get(LOG_LEVEL_ENV))


@contextmanager
def verbose_logging(enabled: bool) -> Iterator[None]:
    """Temporarily enable DEBUG logging for one CLI command invocation."""
    if not enabled:
        yield
        return

    previous_level = os.environ.get(LOG_LEVEL_ENV)
    os.environ[LOG_LEVEL_ENV] = "DEBUG"
    try:
        yield
    finally:
        if previous_level is None:
            os.environ.pop(LOG_LEVEL_ENV, None)
        else:
            os.environ[LOG_LEVEL_ENV] = previous_level


def should_log(level: str) -> bool:
    """Return True when a message should be emitted at the active log level."""
    return normalize_log_level(level) >= current_log_level()


def enable_windows_ansi(stream: TextIO) -> bool:
    """Enable ANSI colors on Windows consoles when the stream supports VT mode."""
    try:
        fileno = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False

    cached = WINDOWS_COLOR_SUPPORT.get(fileno)
    if cached is not None:
        return cached

    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(fileno)
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            WINDOWS_COLOR_SUPPORT[fileno] = False
            return False

        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if new_mode != mode.value and not kernel32.SetConsoleMode(handle, new_mode):
            WINDOWS_COLOR_SUPPORT[fileno] = False
            return False
    except (AttributeError, ImportError, OSError, ValueError):
        WINDOWS_COLOR_SUPPORT[fileno] = False
        return False

    WINDOWS_COLOR_SUPPORT[fileno] = True
    return True


def stream_supports_color(stream: TextIO) -> bool:
    """Return True when ANSI colors should be emitted for the target stream."""
    if os.environ.get("NO_COLOR") is not None:
        return False

    isatty = getattr(stream, "isatty", None)
    if isatty is None or not isatty():
        return False

    if os.name != "nt":
        return os.environ.get("TERM", "").lower() != "dumb"

    # Windows terminals need VT mode enabled before ANSI colors render correctly.
    return enable_windows_ansi(stream)


def format_log_message(level: str, message: str, stream: TextIO) -> str:
    """Wrap a log message in a level-specific ANSI color when supported."""
    color = LOG_LEVEL_COLORS.get(level.strip().upper())
    if color is None or not stream_supports_color(stream):
        return message
    return f"{color}{message}{ANSI_RESET}"


def log_message(level: str, message: str, *, stream: TextIO | None = None) -> None:
    """Print a message only when it meets the configured log threshold."""
    if not should_log(level):
        return
    target_stream = stream or sys.stdout
    # Clear and redraw active progress bars so logs do not append to their current line.
    tqdm.write(format_log_message(level, message, target_stream), file=target_stream)


def log_debug(message: str) -> None:
    """Print a debug message when DEBUG logging is enabled."""
    log_message("DEBUG", message)


def log_info(message: str) -> None:
    """Print an informational message when INFO logging is enabled."""
    log_message("INFO", message)


def log_success(message: str) -> None:
    """Print a success/progress message when INFO logging is enabled."""
    log_message("SUCCESS", message)


def log_warning(message: str) -> None:
    """Print a warning message; warnings are visible at the default level."""
    log_message("WARNING", message, stream=sys.stderr)


def log_error(message: str) -> None:
    """Print an error message; errors are visible at the default level."""
    log_message("ERROR", message, stream=sys.stderr)
