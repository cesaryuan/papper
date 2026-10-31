"""Verify the real PDF preview endpoint across unsaved edits and dependency changes.

These service contracts cover stale native state, source preservation and error
recovery that one-shot DOCX exports cannot reproduce. No benchmark-only protocol
or general persistent DOCX export entry point is required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from lxml import etree
import pymupdf
import pytest

from native_support import papper_command


class PreviewSession:
    """Own one real preview service and read its versioned startup message with a deadline."""

    def __init__(self, project: Path) -> None:
        """Start Papper in an isolated project without modifying the saved buffer during requests."""
        self.project = project
        self.source = project / "manuscript.md"
        self.source.write_text("Saved source must stay untouched\n", encoding="utf-8")
        self.work = project / "service-work"
        self.environment = {**os.environ, "PAPPER_HOME": str(project / "state"), "PAPPER_DISABLE_UPDATE_CHECK": "1"}
        self.log = (project / "preview.log").open("w", encoding="utf-8")
        self.token = "ab" * 32
        self.process = subprocess.Popen(
            [*papper_command(), "__docx_preview_server", "--token", self.token, "--work-dir", str(self.work)],
            cwd=project, env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.log, text=True, encoding="utf-8", bufsize=1,
        )
        self.responses: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        line = self.responses.get(timeout=60)
        assert line, "Preview service exited before readiness"
        self.ready = json.loads(line)
        assert self.ready["protocol"] == "papper-docx-preview-v1"

    def read(self) -> None:
        """Drain startup output so unexpected diagnostics cannot deadlock the owned process."""
        assert self.process.stdout
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put("")

    def preview(self, text: str, *, token: str | None = None) -> tuple[bytes, dict]:
        """Submit an unsaved snapshot through the production authenticated HTTP endpoint."""
        request = Request(
            self.ready["url"] + "/preview",
            data=json.dumps({"source": str(self.source), "source_text": text}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + (token or self.token)},
        )
        with urlopen(request, timeout=60) as response:
            return response.read(), dict(response.headers)

    def close(self) -> None:
        """Close the editor-owned stdin and require normal shutdown before checking file cleanup."""
        if self.process.poll() is None:
            assert self.process.stdin
            self.process.stdin.close()
            try:
                assert self.process.wait(timeout=15) == 0
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.log.close()
        assert not list(self.work.iterdir())


@pytest.fixture
def session(tmp_path: Path) -> PreviewSession:
    """Close every real service even when a rendering assertion fails."""
    (tmp_path / "style.yml").write_text("mathtype: false\n", encoding="utf-8")
    client = PreviewSession(tmp_path)
    try:
        yield client
    finally:
        client.close()


def pdf_text(data: bytes) -> str:
    """Read displayed text from the actual PDF instead of relying on an intermediate response."""
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        return "\n".join(page.get_text() for page in pdf)


def native_fields(path: Path) -> list[str]:
    """Read Word field instructions from a generated document to verify export isolation."""
    with ZipFile(path) as package:
        root = etree.fromstring(package.read("word/document.xml"))
        return root.xpath('//*[local-name()="instrText"]/text()')


def test_unsaved_preview_clears_previous_page_stories(session: PreviewSession) -> None:
    """A later buffer must not inherit headers or footers from a previous warm request."""
    first = ('---\ntitle: Header A\npapperSettings:\n  docxHeader:\n    content: "PRIVATE HEADER A"\n'
             '  docxFooter:\n    content: "PRIVATE FOOTER A"\n---\n\nFirst unsaved body.\n')
    before, first_headers = session.preview(first)
    assert "PRIVATE HEADER A" in pdf_text(before) and "PRIVATE FOOTER A" in pdf_text(before)
    after, second_headers = session.preview('---\ntitle: Header B\n---\n\nSecond unsaved body with $x^2$.\n')
    visible = pdf_text(after)
    assert "Second unsaved body" in visible and "PRIVATE HEADER A" not in visible and "PRIVATE FOOTER A" not in visible
    assert first_headers["X-Papper-Worker-Pid"] == second_headers["X-Papper-Worker-Pid"]
    assert session.source.read_text(encoding="utf-8") == "Saved source must stay untouched\n"


def test_preview_refreshes_bibliography_and_recovers_after_failure(session: PreviewSession) -> None:
    """Same-path bibliography edits invalidate cached citations; a failed request permits later recovery."""
    references = session.project / "references.bib"
    references.write_text('@article{sample, title={Original Title}, author={Example, Alice}, year={2020}}', encoding="utf-8")
    source = '---\nbibliography: references.bib\n---\n\nStudy [@sample].\n'
    first, first_headers = session.preview(source)
    stamp = references.stat()
    references.write_text(references.read_text(encoding="utf-8").replace("Original Title", "Revised  Title"), encoding="utf-8")
    os.utime(references, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    changed, changed_headers = session.preview(source)
    assert "Original title" in pdf_text(first) and "Revised" in pdf_text(changed)
    with pytest.raises(HTTPError) as failure:
        session.preview(source.replace("references.bib", "missing.bib"))
    assert failure.value.code == 422
    recovered, final_headers = session.preview(source + "\nRecovery prose.\n")
    assert "Recovery prose" in pdf_text(recovered)
    assert first_headers["X-Papper-Worker-Pid"] == changed_headers["X-Papper-Worker-Pid"] == final_headers["X-Papper-Worker-Pid"]


def test_preview_rereads_style_without_creating_project_outputs(session: PreviewSession) -> None:
    """An open preview applies changed text margins without creating manuscript exports."""
    style = session.project / "style.yml"
    style.write_text("mathtype: false\ndocxPageMargins:\n  left: 2cm\n", encoding="utf-8")
    first, _ = session.preview("Page size probe.\n")
    style.write_text("mathtype: false\ndocxPageMargins:\n  left: 4cm\n", encoding="utf-8")
    second, _ = session.preview("Page size probe.\n")
    with pymupdf.open(stream=first, filetype="pdf") as before, pymupdf.open(stream=second, filetype="pdf") as after:
        assert after[0].search_for("Page size probe")[0].x0 - before[0].search_for("Page size probe")[0].x0 > 50
    assert not list(session.project.glob("*.docx")) and not list(session.project.glob("*.pdf"))


def test_preview_static_references_do_not_change_formal_export(session: PreviewSession) -> None:
    """Preview uses static numbers even when formal exports before and after it use native fields."""
    text = ('---\npapperSettings:\n  docxNativeCrossref: true\n---\n\n'
            'See @tbl:values.\n\n| Value |\n| --- |\n| 42 |\n\n: Measurements {#tbl:values}\n')
    export_source = session.project / "export.md"
    export_source.write_text(text, encoding="utf-8")
    for name in ("before.docx", "after.docx"):
        if name == "after.docx":
            pdf, _ = session.preview(text)
            assert "Measurements" in pdf_text(pdf)
            # Inspect actual DOCX content consumed by the renderer: cached Word
            # field results can look correct in a PDF even if fields were left on.
            fields = native_fields(next(session.work.rglob("preview.docx")))
            assert not any("SEQ" in code or "REF " in code for code in fields)
        output = session.project / name
        subprocess.run([*papper_command(), "build", "docx", str(export_source), "-o", str(output)],
                       cwd=session.project, env=session.environment, check=True, capture_output=True, timeout=60)
        assert any("SEQ" in code or "REF " in code for code in native_fields(output))
    assert export_source.read_text(encoding="utf-8") == text


def test_preview_authentication_images_and_error_recovery(session: PreviewSession) -> None:
    """Real PDF pixels retain SVG pictures; unauthorized and malformed requests do not poison the service."""
    (session.project / "picture.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="blue"/>'
        '<text x="2" y="14" fill="white">SVG</text></svg>', encoding="utf-8")
    with pytest.raises(HTTPError) as unauthorized:
        session.preview("Rejected", token="00" * 32)
    assert unauthorized.value.code == 401
    first, first_headers = session.preview("First unsaved preview.\n\n![Picture](picture.svg)\n")
    with pymupdf.open(stream=first, filetype="pdf") as pdf:
        assert "First unsaved preview" in "".join(page.get_text() for page in pdf)
        # Supported SVG can become vector paths. Count rendered blue pixels,
        # not image objects, so both vector rendering and PNG fallback are valid.
        pixels = pdf[0].get_pixmap(alpha=False).samples
        blue = sum(b > 180 and r < 80 and g < 80 for r, g, b in zip(pixels[0::3], pixels[1::3], pixels[2::3]))
        assert blue > 100, "Preview lost the SVG picture"
    with pytest.raises(HTTPError) as invalid:
        session.preview("---\ntitle: [invalid\n---\n")
    assert invalid.value.code == 422
    second, second_headers = session.preview("Second unsaved preview.\n")
    assert first_headers["X-Papper-Worker-Pid"] == second_headers["X-Papper-Worker-Pid"]
    assert "Second unsaved preview" in pdf_text(second) and "First unsaved preview" not in pdf_text(second)
    assert session.source.read_text(encoding="utf-8") == "Saved source must stay untouched\n"
