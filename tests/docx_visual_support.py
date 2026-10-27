"""Independent Word and DOCXIDE visual baselines with shared exact PDF-page comparisons."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

import pymupdf
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from native_support import ROOT
from visual_support import assert_visual, record_visual_changes


VISUAL_ROOT = Path(__file__).with_name("visual")
VISUAL_PLATFORM = f"{sys.platform}-{platform.machine().lower()}"
DOCX_ENVIRONMENT = VISUAL_ROOT / "environments" / VISUAL_PLATFORM / "docx-word.json"
LOGGER = logging.getLogger(__name__)


class DocxVisualSession:
    """Share rasterization and baseline review without requiring a Word application."""

    def validate_environment(self) -> None:
        """Reject unreviewed renderer, font or rasterizer changes before comparing pixels."""
        if self.update:
            return
        assert self.environment_path.exists(), (
            f"Missing {self.backend} visual environment: {self.environment_path}; use --visual-update explicitly"
        )
        expected = json.loads(self.environment_path.read_text(encoding="utf-8"))["environment"]
        assert expected == self.environment, (
            f"DOCX {self.backend} rendering environment differs; review the renderer, fonts and rasterizer\n"
            + json.dumps({"expected": expected, "actual": self.environment}, indent=2)
        )

    @staticmethod
    def _font_identity() -> str:
        """Hash installed machine/user fonts so substitutions cannot silently update layout."""
        if sys.platform != "win32":
            directories = (
                Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
                Path.home() / ".fonts", Path.home() / ".local/share/fonts",
                Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
            )
            return DocxVisualSession._hash_font_paths({
                path for directory in directories for path in directory.rglob("*")
                if path.is_file() and path.suffix.lower() in {".ttf", ".ttc", ".otf"}
            })
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
        return DocxVisualSession._hash_font_paths(paths)

    @staticmethod
    def _hash_font_paths(paths: set[Path]) -> str:
        """Hash ordered font names and bytes consistently across installed-font discovery methods."""
        digest = hashlib.sha256()
        for path in sorted(paths, key=lambda item: str(item).lower()):
            digest.update(path.name.lower().encode("utf-8"))
            digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing")
        assert paths, "No installed fonts found"
        return digest.hexdigest()

    @staticmethod
    def render_pdf(path: Path) -> tuple[list[bytes], list[dict]]:
        """Rasterize every PDF page at fixed DPI, preserving page count and physical size."""
        images, geometry = [], []
        with pymupdf.open(path) as document:
            assert document.page_count > 0, f"DOCX PDF has no pages: {path}"
            assert any(page.get_text().strip() for page in document), f"DOCX PDF lost all document text: {path}"
            for page in document:
                pixmap = page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False)
                images.append(pixmap.tobytes("png"))
                geometry.append({"width_pt": page.rect.width, "height_pt": page.rect.height,
                                 "width_px": pixmap.width, "height_px": pixmap.height})
        return images, geometry

    def check(self, source: Path, baseline: Path, *, update_fields: bool = False) -> None:
        """Compare page pixels/geometry and save only per-page diffs against Git HEAD."""
        images, geometry = self.capture(source, source.parent / f"{self.backend}-render", update_fields=update_fields)
        manifest = baseline / "pages.json"
        record_visual_changes(
            {f"page-{index:03d}.png": image for index, image in enumerate(images, 1)},
            baseline,
        )
        if self.update:
            baseline.mkdir(parents=True, exist_ok=True)
            for index, image in enumerate(images, 1):
                (baseline / f"page-{index:03d}.png").write_bytes(image)
            # A shorter intentional document must discard only superseded DOCX page PNGs.
            for stale in baseline.glob("page-*.png"):
                # Diff images for deleted pages must survive baseline updates for review.
                if stale.name.endswith("-diff.png"):
                    continue
                if stale.name not in {f"page-{index:03d}.png" for index in range(1, len(images) + 1)}:
                    stale.unlink()
            manifest_data = {"pages": geometry}
            if update_fields:
                manifest_data["field_update"] = self.field_update_description
            manifest.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
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
                # Temporary harness baselines have no Git version; compare their local PNGs.
                diff_path = None if baseline.resolve().is_relative_to(ROOT) else baseline / f"page-{index:03d}-diff.png"
                assert_visual(image, expected, diff_path)
            except AssertionError as error:
                failures.append(str(error))
        if failures:
            raise AssertionError("\n".join(failures))

    def save_environment(self) -> None:
        """Record this renderer environment only when explicitly updating its baselines."""
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        self.environment_path.parent.mkdir(parents=True, exist_ok=True)
        self.environment_path.write_text(json.dumps({"source_commit": revision, "environment": self.environment}, indent=2) + "\n", encoding="utf-8")


class WordVisualSession(DocxVisualSession):
    """Own export requests and compare Word's real paginated pages with reviewed PNGs."""

    backend = "word"
    environment_path = DOCX_ENVIRONMENT
    field_update_description = "Word fields and directories updated until pagination stabilizes; DATE locked to fixture cache"

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
        self.validate_environment()

    def _word_request(self, mode: str, directory: Path, source: Path | None = None, *, update_fields: bool = False) -> dict:
        """Invoke a real PowerShell file with JSON paths; never interpolate COM into a shell."""
        directory.mkdir(parents=True, exist_ok=True)
        result_path = directory / "word-result.json"
        request = {"mode": mode, "result": str(result_path.resolve())}
        if source is not None:
            request.update(source=str(source.resolve()), output=str((directory / "rendered.pdf").resolve()))
            request["update_fields"] = update_fields
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

    def capture(self, source: Path, directory: Path, *, update_fields: bool = False) -> tuple[list[bytes], list[dict]]:
        """Require two independent Word exports to agree before trusting any baseline."""
        source_digest = hashlib.sha256(source.read_bytes()).digest()
        renders = []
        for attempt in (1, 2):
            export_dir = directory / f"export-{attempt}"
            environment = self._word_request("export", export_dir, source, update_fields=update_fields)
            assert environment == self.word, "Word renderer/printer/options changed during DOCX export"
            renders.append(self.render_pdf(export_dir / "rendered.pdf"))
        assert hashlib.sha256(source.read_bytes()).digest() == source_digest, "Word export modified the generated DOCX"
        assert renders[0] == renders[1], f"Word exports did not produce stable page pixels: {directory}"
        return renders[1]


class DocxideSettings(BaseSettings):
    """Locate the external renderer without adding it to Papper's product dependencies."""

    model_config = SettingsConfigDict(env_prefix="PAPPER_DOCXIDE_")
    renderer: Path = Field(default=ROOT / "tests/visual/.tools/docxide/bin" / ("docxide-pdf.exe" if os.name == "nt" else "docxide-pdf"))  # Cargo-installed renderer
    timeout: float = Field(default=90, gt=0)  # Maximum seconds per independent export


class DocxideVisualSession(DocxVisualSession):
    """Render with DOCXIDE and compare only against its own reviewed page baselines."""

    backend = "docxide"
    environment_path = VISUAL_ROOT / "environments" / VISUAL_PLATFORM / "docx-docxide.json"
    field_update_description = "DOCXIDE evaluates supported fields automatically; unsupported fields retain caches; DATE locked to fixture cache"

    def __init__(self, work: Path, update: bool) -> None:
        """Identify the Cargo installation, binary and fonts independently from Word."""
        self.work = work
        self.update = update
        self.settings = DocxideSettings()
        self.renderer = self.settings.renderer.resolve()
        assert self.renderer.is_file(), (
            f"Missing DOCXIDE renderer: {self.renderer}; install the pinned build in tests/visual/README.md "
            "or set PAPPER_DOCXIDE_RENDERER"
        )
        metadata = self.renderer.parent.parent / ".crates2.json"
        assert metadata.is_file(), f"Missing Cargo installation identity: {metadata}; use cargo install --root"
        installations = json.loads(metadata.read_text(encoding="utf-8"))["installs"]
        candidates = [(key, value) for key, value in installations.items()
                      if key.startswith("docxide-pdf ") and self.renderer.name in value["bins"]]
        assert len(candidates) == 1, f"Ambiguous DOCXIDE installation identity: {metadata}"
        package, installation = candidates[0]
        extra_fonts = {}
        for value in os.environ.get("DOCXIDE_FONTS", "").split(os.pathsep):
            if value:
                directory = Path(value).resolve()
                assert directory.is_dir(), f"Missing DOCXIDE_FONTS directory: {directory}"
                extra_fonts[str(directory)] = {
                    str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(directory.rglob("*"))
                    if path.is_file() and path.suffix.lower() in {".ttf", ".ttc", ".otf"}
                }
        self.environment = {
            "platform": sys.platform, "architecture": platform.machine().lower(),
            "os_release": platform.release(), "renderer": package,
            "installation": installation,
            "renderer_sha256": hashlib.sha256(self.renderer.read_bytes()).hexdigest(),
            "installed_fonts_sha256": self._font_identity(),
            "extra_fonts_sha256": extra_fonts,
            "pymupdf": pymupdf.VersionBind, "mupdf": pymupdf.VersionFitz,
            "dpi": 144, "colorspace": "RGB", "alpha": False,
            "export": "DOCXIDE CLI PDF; automatic supported-field evaluation",
        }
        self.validate_environment()
        LOGGER.info("DOCXIDE visual renderer: %s (%s)", package, self.renderer)

    def capture(self, source: Path, directory: Path, *, update_fields: bool = False) -> tuple[list[bytes], list[dict]]:
        """Require two fresh CLI exports to agree and preserve the original DOCX bytes."""
        source_digest = hashlib.sha256(source.read_bytes()).digest()
        renders = []
        for attempt in (1, 2):
            export_dir = directory / f"export-{attempt}"
            export_dir.mkdir(parents=True, exist_ok=True)
            output = export_dir / "rendered.pdf"
            # DOCXIDE adds numbered suffixes instead of overwriting, so remove only
            # this test-owned PDF to prevent reading a stale export on a repeated check.
            output.unlink(missing_ok=True)
            try:
                result = subprocess.run(
                    [str(self.renderer), str(source.resolve()), str(output.resolve())],
                    cwd=export_dir, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=self.settings.timeout,
                )
            except subprocess.TimeoutExpired as error:
                raise AssertionError(f"DOCXIDE PDF export timed out: {export_dir}") from error
            (export_dir / "renderer.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            assert result.returncode == 0, f"DOCXIDE export failed:\n{result.stdout}\n{result.stderr}"
            assert output.is_file(), f"DOCXIDE did not produce the requested PDF: {output}"
            renders.append(self.render_pdf(output))
        assert hashlib.sha256(source.read_bytes()).digest() == source_digest, "DOCXIDE export modified the generated DOCX"
        assert renders[0] == renders[1], f"DOCXIDE exports did not produce stable page pixels: {directory}"
        return renders[1]
