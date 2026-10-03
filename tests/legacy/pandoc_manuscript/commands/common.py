"""Shared helpers used by multiple papper command modules."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

from pydantic import Field
from pydantic_settings import BaseSettings


PANDOC_CROSSREF_VERSION_WARNING_PATTERN = re.compile(
    r"WARNING: pandoc-crossref was compiled with pandoc \S+ but is being run through \S+\. "
    r"This is not supported\. Strange things may \(and likely will\) happen silently\."
)


class VerboseCommandSettings(BaseSettings):
    """Base settings shared by papper subcommands that support detailed logging."""

    verbose: bool = Field(default=False, description="Enable detailed debug logging.")


def suppress_known_external_warnings(output: str) -> str:
    """Remove the repeated pandoc-crossref ABI warning while preserving other output."""
    # Managed Pandoc and pandoc-crossref releases can report different embedded
    # Pandoc versions even when a build succeeds, and every probe repeats it.
    return "".join(
        line
        for line in output.splitlines(keepends=True)
        if PANDOC_CROSSREF_VERSION_WARNING_PATTERN.fullmatch(line.strip()) is None
    )


def forward_filtered_process_stream(source: TextIO, target: TextIO) -> None:
    """Forward one subprocess stream while suppressing known repeated warnings."""
    try:
        for line in source:
            filtered = suppress_known_external_warnings(line)
            if filtered:
                print(filtered, end="", file=target, flush=True)
    finally:
        source.close()


def run_streaming_command(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with filtered stdout and stderr forwarded in real time."""
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Could not capture subprocess output streams.")

    threads = [
        threading.Thread(
            target=forward_filtered_process_stream,
            args=(process.stdout, sys.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=forward_filtered_process_stream,
            args=(process.stderr, sys.stderr),
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()

    try:
        returncode = process.wait()
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        for thread in threads:
            thread.join()

    result = subprocess.CompletedProcess(command, returncode)
    if check:
        result.check_returncode()
    return result


def log(message: str) -> None:
    """Print a papper CLI status line."""
    print(message)


@contextmanager
def project_directory(project_dir: Path) -> Iterator[None]:
    """Temporarily run command logic from the selected manuscript project."""
    previous_cwd = Path.cwd()
    os.chdir(project_dir)
    try:
        yield
    finally:
        os.chdir(previous_cwd)
