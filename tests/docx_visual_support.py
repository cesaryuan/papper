"""Word PDF export and deterministic page rendering for DOCX visual contracts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

from native_support import ROOT
from visual_support import assert_visual


VISUAL_ROOT = Path(__file__).with_name("visual")
VISUAL_PLATFORM = f"{sys.platform}-{platform.machine().lower()}"
DOCX_ENVIRONMENT = VISUAL_ROOT / "environments" / VISUAL_PLATFORM / "docx-word.json"
LOGGER = logging.getLogger(__name__)


class WordVisualSession:
    """Own export requests and compare Word's real paginated pages with reviewed PNGs."""

    def __init__(self, work: Path, update: bool) -> None:
        """Validate renderer, printer, rasterizer and installed font identities up front."""
        assert sys.platform == "win32", "DOCX visual tests currently require Windows and Microsoft Word"
        self.work = work
        self.update = update
        self.word = self._word_request("probe", work / "probe")
        self.environment = {
            "platform": sys.platform, "architecture": platform.machine().lower(),
            "os_release": platform.release(), "word": self.word,
            "pymupdf": pymupdf.VersionBind, "mupdf": pymupdf.VersionFitz,
            "dpi": 144, "colorspace": "RGB", "alpha": False,
            "export": "print-quality document-content without review markup; repaginate; no field update",
            "installed_fonts_sha256": self._font_identity(),
        }
        if not update:
            assert DOCX_ENVIRONMENT.exists(), f"Missing DOCX visual environment: {DOCX_ENVIRONMENT}; use --visual-update explicitly"
            expected = json.loads(DOCX_ENVIRONMENT.read_text(encoding="utf-8"))["environment"]
            assert expected == self.environment, (
                "DOCX rendering environment differs; use the recorded Word build, printer, fonts and PyMuPDF\n"
                + json.dumps({"expected": expected, "actual": self.environment}, indent=2)
            )

    @staticmethod
    def _font_identity() -> str:
        """Hash installed machine/user fonts so substitutions cannot silently update layout."""
        import winreg

        paths: set[Path] = set()
        machine_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        user_fonts = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Microsoft/Windows/Fonts"
        for directory in (machine_fonts, user_fonts):
            paths.update(path for path in directory.glob("*") if path.suffix.lower() in {".ttf", ".ttc", ".otf"})
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            except FileNotFoundError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    _, value, _ = winreg.EnumValue(key, index)
                    # Font registration also includes DWORD settings, not file paths.
                    if not isinstance(value, str) or not value:
                        continue
                    path = Path(os.path.expandvars(value))
                    paths.add(path if path.is_absolute() else machine_fonts / path)
        digest = hashlib.sha256()
        for path in sorted(paths, key=lambda item: str(item).lower()):
            digest.update(path.name.lower().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing")
        assert paths, "No installed Windows fonts found"
        return digest.hexdigest()

    def _word_request(self, mode: str, directory: Path, source: Path | None = None) -> dict:
        """Invoke a real PowerShell file with JSON paths; never interpolate COM into a shell."""
        directory.mkdir(parents=True, exist_ok=True)
        result_path = directory / "word-result.json"
        request = {"mode": mode, "result": str(result_path.resolve())}
        if source is not None:
            request.update(source=str(source.resolve()), output=str((directory / "rendered.pdf").resolve()))
        request_path = directory / "word-request.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(VISUAL_ROOT / "export_word_pdf.ps1"), "-RequestPath", str(request_path)]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        except subprocess.TimeoutExpired as error:
            # Never kill Word to recover a helper timeout: it may contain user work.
            raise AssertionError(f"Word PDF export timed out; Word was left running, inspect test-owned documents: {directory}") from error
        assert completed.returncode == 0, f"Word {mode} failed:\n{completed.stdout}\n{completed.stderr}"
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        assert result["user_documents_preserved"], "Word export did not preserve pre-existing documents"
        return result["environment"]

    @staticmethod
    def render_pdf(path: Path) -> tuple[list[bytes], list[dict]]:
        """Rasterize every PDF page at fixed DPI, preserving page count and physical size."""
        images, geometry = [], []
        with pymupdf.open(path) as document:
            assert document.page_count > 0, f"Word PDF has no pages: {path}"
            assert any(page.get_text().strip() for page in document), f"Word PDF lost all document text: {path}"
            for page in document:
                pixmap = page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)
                images.append(pixmap.tobytes("png"))
                geometry.append({"width_pt": page.rect.width, "height_pt": page.rect.height,
                                 "width_px": pixmap.width, "height_px": pixmap.height})
        return images, geometry

    def capture(self, source: Path, directory: Path) -> tuple[list[bytes], list[dict]]:
        """Require two independent Word exports to agree before trusting any baseline."""
        source_digest = hashlib.sha256(source.read_bytes()).digest()
        renders = []
        for attempt in (1, 2):
            export_dir = directory / f"export-{attempt}"
            environment = self._word_request("export", export_dir, source)
            assert environment == self.word, "Word renderer/printer/options changed during DOCX export"
            renders.append(self.render_pdf(export_dir / "rendered.pdf"))
        assert hashlib.sha256(source.read_bytes()).digest() == source_digest, "Word export modified the generated DOCX"
        assert renders[0] == renders[1], f"Word exports did not produce stable page pixels: {directory}"
        return renders[1]

    def check(self, source: Path, baseline: Path, results: Path) -> None:
        """Compare all pages and collect every failing page's images and geometry."""
        images, geometry = self.capture(source, source.parent / "word-render")
        manifest = baseline / "pages.json"
        if self.update:
            baseline.mkdir(parents=True, exist_ok=True)
            for index, image in enumerate(images, 1):
                (baseline / f"page-{index:03d}.png").write_bytes(image)
            # A shorter intentional document must discard only superseded DOCX page PNGs.
            for stale in baseline.glob("page-*.png"):
                if stale.name not in {f"page-{index:03d}.png" for index in range(1, len(images) + 1)}:
                    stale.unlink()
            manifest.write_text(json.dumps({"pages": geometry}, indent=2) + "\n", encoding="utf-8")
            self.save_environment()
            LOGGER.info("Updated DOCX visual baseline: %s (%s pages)", baseline, len(images))
            return
        assert manifest.exists(), f"Missing DOCX visual baseline: {baseline}; use --visual-update explicitly"
        expected_geometry = json.loads(manifest.read_text(encoding="utf-8"))["pages"]
        failures = []
        if geometry != expected_geometry:
            failures.append(f"DOCX page count/geometry changed: {len(expected_geometry)} -> {len(geometry)} pages")
        for index, image in enumerate(images, 1):
            expected = baseline / f"page-{index:03d}.png"
            if not expected.exists():
                failures.append(f"Missing expected page: {expected}")
                continue
            try:
                assert_visual(image, expected, results / f"page-{index:03d}")
            except AssertionError as error:
                failures.append(str(error))
        if failures:
            results.mkdir(parents=True, exist_ok=True)
            for index, image in enumerate(images, 1):
                page_dir = results / f"page-{index:03d}"
                page_dir.mkdir(exist_ok=True)
                (page_dir / "actual.png").write_bytes(image)
            shutil.copyfile(source.parent / "word-render/export-2/rendered.pdf", results / "actual.pdf")
            (results / "pages.json").write_text(json.dumps({"expected": expected_geometry, "actual": geometry}, indent=2) + "\n", encoding="utf-8")
            raise AssertionError("\n".join(failures))

    def save_environment(self) -> None:
        """Record Word environment separately from Chromium when explicitly updating pages."""
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        DOCX_ENVIRONMENT.parent.mkdir(parents=True, exist_ok=True)
        DOCX_ENVIRONMENT.write_text(json.dumps({"source_commit": revision, "environment": self.environment}, indent=2) + "\n", encoding="utf-8")
