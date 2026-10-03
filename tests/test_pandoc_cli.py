"""Verify the shared native binary's public CLI contracts against matching Pandoc.

These real-process checks cover stdin/stdout, errors, binary DOCX output/media
extraction, and embedded crossref ordering. They complement manuscript snapshots
and the installed-wheel smoke check, which do not exercise general CLI dispatch.
Build scripts/pandoc-server before running this module with ``uv run pytest``.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from zipfile import ZipFile

import pytest

from pandoc_manuscript.runtime.resources import native_pandoc_executable


@pytest.fixture(scope="module")
def engine() -> Path:
    """Require a real shared native binary; never replace conversion with a mock."""
    root = Path(__file__).resolve().parents[1]
    selected = root / ".pmt/pandoc-worker/current.json"
    if selected.is_file():
        record = json.loads(selected.read_text(encoding="utf-8"))
        return selected.parent / record["executable"]
    executable = native_pandoc_executable()
    if executable is None:
        pytest.skip("Build scripts/pandoc-server to exercise the native Pandoc CLI")
    return executable


@pytest.fixture(scope="module")
def official_pandoc(engine: Path) -> str:
    """Use the same upstream release as a behavioral reference when installed."""
    executable = os.environ.get("PAPPER_TEST_PANDOC") or shutil.which("pandoc")
    if executable is None:
        pytest.skip("CLI reference comparisons require an official Pandoc")
    # Match the linked release so a dependency upgrade cannot compare different
    # upstream semantics. The override allows testing without replacing user tools.
    linked = subprocess.run([str(engine), "--version"], capture_output=True, text=True, check=True)
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True)
    expected = linked.stdout.splitlines()[0]
    if version.stdout.splitlines()[0] != expected:
        pytest.skip(f"CLI reference comparisons require exactly {expected}")
    return executable


class NativeCLI:
    """Run bounded CLI conversions without polluting Pandoc's standard streams."""

    @staticmethod
    def run(executable: str | Path, args: list[str], project: Path, source: str = "") -> subprocess.CompletedProcess[str]:
        """Capture actual output/errors with build-store resources deliberately unavailable."""
        return subprocess.run(
            [str(executable), *args], input=source, cwd=project, capture_output=True,
            text=True, encoding="utf-8", timeout=30,
            env={**os.environ, "pandoc_datadir": str(project / "missing-pandoc-data")},
        )

    @staticmethod
    def http_conversion(executable: str | Path, project: Path) -> bytes:
        """Start an owned upstream HTTP server, convert once, and always stop it."""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [str(executable), "server", "--port", str(port)], cwd=project,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/", data=json.dumps({"text": "**CLI server**", "to": "html"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    with opener.open(request, timeout=2) as response:
                        return response.read()
                except urllib.error.URLError:
                    if process.poll() is not None:
                        raise AssertionError(process.stderr.read().decode(errors="replace"))
                    time.sleep(0.05)
            raise AssertionError("The Pandoc CLI HTTP server did not become ready")
        finally:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.parametrize("args", [
    ["-f", "markdown", "-t", "html"],
    ["-f", "markdown", "-t", "json"],
    ["--help"],
    ["--version"],
    ["server", "--help"],
    ["server", "--version"],
    ["--print-default-data-file", "templates/styles.citations.html"],
    ["--unknown-papper-test-option"],
    ["-f", "unsupported-papper-reader", "-t", "html"],
])
def test_cli_matches_upstream_streams_and_exit_codes(engine, official_pandoc, tmp_path, args) -> None:
    """Preserve upstream text, JSON, information requests, and conversion failure behavior."""
    source = "# Unicode 输入\n\nProse with *emphasis* and $x_1$.\n"
    expected = NativeCLI.run(official_pandoc, args, tmp_path, source)
    actual = NativeCLI.run(engine, args, tmp_path, source)
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr)


@pytest.mark.parametrize("arguments", [["-f", "org", "-t", "html"], ["-f", "markdown", "-t", "org"]])
def test_removed_format_preserves_existing_output(engine, tmp_path, arguments) -> None:
    """Removed registry formats must fail explicitly without replacing a user's file."""
    output = tmp_path / "existing-output.txt"
    previous = b"Keep the previously built document\n"
    output.write_bytes(previous)
    actual = NativeCLI.run(engine, [*arguments, "-o", str(output)], tmp_path, "* Test document\n")
    assert actual.returncode != 0
    assert "org" in actual.stderr.lower()
    assert output.read_bytes() == previous


def test_cli_docx_binary_stdout_and_media_extraction(engine, tmp_path) -> None:
    """A binary stdout DOCX must remain valid and import with its media intact."""
    image = tmp_path / "pixel.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
    ))
    source = "Unicode 输入 with **bold**.\n\n![Pixel](pixel.png)\n"
    result = subprocess.run(
        [str(engine), "-f", "markdown", "-t", "docx", "-o", "-"], input=source.encode(),
        cwd=tmp_path, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with ZipFile(io.BytesIO(result.stdout)) as document:
        assert "输入" in document.read("word/document.xml").decode()
        assert any(name.startswith("word/media/") for name in document.namelist())
    docx = tmp_path / "paper.docx"
    docx.write_bytes(result.stdout)
    imported = NativeCLI.run(engine, [str(docx), "-t", "markdown", "--extract-media", "extracted"], tmp_path)
    assert imported.returncode == 0, imported.stderr
    assert "Unicode 输入" in imported.stdout
    assert list((tmp_path / "extracted/media").glob("*.png"))


@pytest.mark.parametrize("filter_name", ["pandoc-crossref", "pandoc-crossref.exe"])
def test_cli_embedded_crossref_preserves_filter_order(engine, tmp_path, filter_name) -> None:
    """Defaults must keep a Lua-before/crossref/Lua-after pipeline without a PATH filter."""
    (tmp_path / "before.lua").write_text(
        "function Table(t) t.identifier = 'tbl:values'; return t end\n", encoding="utf-8",
    )
    (tmp_path / "after.lua").write_text(
        "function Cite(c) error('crossref left an unresolved citation') end\n", encoding="utf-8",
    )
    (tmp_path / "defaults.yml").write_text(
        f"from: markdown\nto: html\nmetadata:\n  linkReferences: true\nfilters:\n"
        f"  - before.lua\n  - {filter_name}\n  - after.lua\n",
        encoding="utf-8",
    )
    source = "| Value |\n|-------|\n| 42 |\n\nTable: Values\n\nSee @tbl:values.\n"
    # Removing PATH filter discovery proves crossref is executed in-process.
    result = subprocess.run(
        [str(engine), "--defaults", "defaults.yml"], input=source, cwd=tmp_path,
        capture_output=True, text=True, encoding="utf-8", timeout=30, env={**os.environ, "PATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert 'href="#tbl:values"' in result.stdout
    assert "@tbl:values" not in result.stdout


def test_cli_lua_mode_matches_upstream(engine, official_pandoc, tmp_path) -> None:
    """The Lua subcommand must expose Pandoc's scripting API with clean stdout."""
    args = ["lua", "-e", "print(pandoc.write(pandoc.read('**CLI Lua**'), 'plain'))"]
    expected = NativeCLI.run(official_pandoc, args, tmp_path)
    actual = NativeCLI.run(engine, args, tmp_path)
    assert expected.returncode == 0, expected.stderr
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr)


def test_cli_http_server_mode_matches_upstream(engine, official_pandoc, tmp_path) -> None:
    """The official server subcommand must preserve its JSON conversion protocol."""
    expected = NativeCLI.http_conversion(official_pandoc, tmp_path)
    actual = NativeCLI.http_conversion(engine, tmp_path)
    assert b"CLI server" in expected
    assert actual == expected
