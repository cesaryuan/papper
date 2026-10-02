"""Exercise the HTML server from an installed platform wheel, without GHC/Cabal.

Run with ``uv run --isolated --no-project --with PATH.whl python
scripts/check_packaged_html_server.py``. The script rejects a source checkout,
clears worker overrides, and builds a temporary manuscript through the real
background HTTP service. It checks rendered citations/cross-references, warm
HTML reuse, and source-edit invalidation, then stops its owned server. Pandoc's
data directory is deliberately unavailable to verify embedded resources. No
standalone Pandoc or pandoc-crossref executable is needed for these conversions.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from lxml import html

from pandoc_manuscript.commands import build, pandoc_server as client
from pandoc_manuscript.commands.pandoc_server_runtime import PandocWorker
from pandoc_manuscript.commands.setup import pandoc_command, setup_pandoc_tools
from pandoc_manuscript.commands.doctor import command_status
from pandoc_manuscript.commands.convert import ConvertSettings
from pandoc_manuscript.commands.build_reply import resolve as reply_resolve
from pandoc_manuscript.html.build import prepare_html_metadata
from pandoc_manuscript.runtime.metadata import write_pandoc_metadata
from pandoc_manuscript.runtime.paths import project_state_dir
from pandoc_manuscript.runtime.resources import package_resource_path, source_tree_root, template_root


class PackagedServerSmoke:
    """Own the installed-wheel fixture, background service, and output assertions."""

    @staticmethod
    def prepare(project: Path) -> tuple[Path, Path]:
        """Write an offline manuscript and the same packaged filters/templates as HTML builds."""
        source = project / "manuscript.md"
        source.write_text(
            "---\ntitle: Packaged worker smoke\nbibliography: references.bib\n"
            f"csl: {package_resource_path('pandoc/csl/sage-vancouver.csl').as_posix()}\n---\n\n"
            "Original wheel prose cites [@packaged].\n\n"
            "| Value |\n|-------|\n| 42    |\n\nTable: Packaged values {#tbl:values}\n\n"
            "See @tbl:values.\n", encoding="utf-8",
        )
        (project / "references.bib").write_text(
            "@article{packaged, author={Developer, Wheel}, title={Bundled citation entry}, "
            "journal={Packaging Journal}, year={2026}, volume={1}, pages={1--2}}\n", encoding="utf-8",
        )
        build.SETTINGS = build.BuildSettings(manuscript_file=str(source))
        effective = build.load_build_metadata()
        prepare_html_metadata(effective, source.stem)
        metadata_file = project / "metadata.yml"
        write_pandoc_metadata(effective.pandoc_metadata, metadata_file)
        resources = [project, template_root()]
        config = client.write_pmt_server_config(
            project_dir=project,
            pandoc_args=["--defaults", str(package_resource_path("pandoc/pandoc-html.yml")),
                         "--metadata-file", str(metadata_file),
                         "--resource-path", os.pathsep.join(map(str, resources))],
            pandoc_metadata=effective.pandoc_metadata, resource_paths=resources,
            metadata_sources={"style_file": "style.yml", "project_name": source.stem},
        )
        return source, config

    @staticmethod
    def check_output(document: str, prose: str) -> None:
        """Require actual bibliography and cross-reference rendering in the output."""
        tree = html.fromstring(document)
        text = tree.text_content()
        assert prose in text, "The server returned stale manuscript prose"
        assert "Bundled citation entry" in text, "Citeproc did not render the bibliography"
        assert tree.xpath("//*[@id='ref-packaged']"), "The bibliography entry is missing"
        assert tree.xpath("//a[@href='#tbl:values']"), "Crossref did not resolve the table reference"
        assert "@tbl:values" not in text, "An unresolved cross-reference reached the HTML"

    @staticmethod
    def check_cli(worker: Path, source: Path, config: Path) -> None:
        """Require offline setup, conversions, and reply JSON probes through the same binary."""
        pandoc, crossref = setup_pandoc_tools(force=True)
        assert Path(pandoc_command()).resolve() == worker, "The CLI selected a second Pandoc engine"
        assert crossref.source == "embedded", "Setup selected an external crossref"
        for name in ("pandoc", "pandoc-crossref"):
            ok, detail = command_status([name, "--version"])
            assert ok, detail
        args = json.loads(config.read_text(encoding="utf-8"))["pandoc_args"]
        output = source.parent / "cli.html"
        subprocess.run([str(pandoc.executable), *args, str(source), "-o", str(output)], check=True)
        PackagedServerSmoke.check_output(output.read_text(encoding="utf-8"), "Original wheel prose")
        docx = source.parent / "cli.docx"
        subprocess.run([
            str(pandoc.executable), str(source), "--filter", "pandoc-crossref", "--citeproc", "-o", str(docx),
        ], check=True)
        result = subprocess.run([str(pandoc.executable), str(docx), "-t", "plain"],
                                capture_output=True, text=True, encoding="utf-8", check=True)
        assert "Bundled citation entry" in result.stdout, "The CLI did not render the DOCX bibliography"
        assert "@tbl:values" not in result.stdout, "The CLI did not resolve the DOCX cross-reference"
        converted = source.parent / "imported"
        assert ConvertSettings(docx=docx, o=converted).run() == 0
        markdown = (converted / "cli.md").read_text(encoding="utf-8")
        assert "Original wheel prose" in markdown, "DOCX import did not use the shared engine successfully"
        style = source.parent / "metadata.yml"
        references = reply_resolve.resolve_reference_map(source, style, ["tbl:values"], "markdown")
        citations = reply_resolve.resolve_citation_map(source, style, ["packaged"], "markdown")
        assert references.get("tbl:values"), "The reply's JSON probe did not resolve its table reference"
        assert citations.get("packaged"), "The reply's JSON probe did not resolve its citation"
        print(f"Packaged Pandoc CLI passed: {worker.name}; offline setup, HTML, DOCX, import, reply probes, crossref")

    @staticmethod
    def run(project: Path) -> None:
        """Start the real service and verify cold, repeated, and edited builds."""
        worker = Path(PandocWorker._resolve_command({})[0]).resolve()
        assert worker.parent == package_resource_path("bin").resolve(), "The wheel's worker was not selected"
        source, config = PackagedServerSmoke.prepare(project)
        PackagedServerSmoke.check_cli(worker, source, config)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        info = None
        state = project_state_dir(project)
        try:
            info = client.ensure_pandoc_server(port=port, config_path=config, wait_seconds=30)
            output = project / "result.html"
            assert client.build_with_pandoc_server(info, source, output), "The PMT server was not used"
            first = output.read_text(encoding="utf-8")
            PackagedServerSmoke.check_output(first, "Original wheel prose")
            request = urllib.request.Request(
                f"{info.base_url}/convert/raw", data=json.dumps({"path": str(source)}).encode(),
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with client._server_opener().open(request, timeout=30) as response:
                assert response.headers.get("X-PMT-Cache") == "hit", "Repeated HTML did not reuse the cache"
                assert response.read().decode("utf-8").rstrip() == first.rstrip(), "Cached HTML changed"
            source.write_text(source.read_text(encoding="utf-8").replace("Original wheel prose", "Edited wheel prose"),
                              encoding="utf-8")
            assert client.build_with_pandoc_server(info, source, output)
            edited = output.read_text(encoding="utf-8")
            PackagedServerSmoke.check_output(edited, "Edited wheel prose")
            assert "Original wheel prose" not in html.fromstring(edited).text_content()
            print(f"Packaged HTML server passed: {worker.name}; citations, crossref, cache reuse, edit invalidation")
        except BaseException:
            for filename in ("pandoc-server.log", "pandoc-server-worker.log"):
                log = state / filename
                if log.is_file():
                    print(log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise
        finally:
            if info is not None:
                client._stop_pid(info.pid)
            # The unique temporary project owns this state; retain no user cache.
            shutil.rmtree(state, ignore_errors=True)


def main() -> int:
    """Verify the installed distribution independently of source-tree tool overrides."""
    if source_tree_root() is not None:
        raise RuntimeError("Run this smoke check with an installed wheel, not the editable source package")
    original_dir = Path.cwd()
    overrides = {key: os.environ.pop(key, None) for key in
                 ("PMT_PANDOC_SERVER_WORKER_COMMAND", "PMT_PANDOC_SERVER_COMMAND", "pandoc_datadir")}
    try:
        with tempfile.TemporaryDirectory(prefix="papper-wheel-server-") as temporary:
            project = Path(temporary).resolve()
            # A build runner's Cabal store can hide missing wheel resources.
            # Require embedded Pandoc data even while that store still exists.
            os.environ["pandoc_datadir"] = str(project / "missing-pandoc-data")
            os.chdir(project)
            try:
                PackagedServerSmoke.run(project)
            finally:
                # Windows cannot remove a directory that is still our cwd;
                # restore it before TemporaryDirectory cleans up on failure.
                os.chdir(original_dir)
    finally:
        os.chdir(original_dir)
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
