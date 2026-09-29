"""Line-source caching and PDF line-number resolution for reply builds."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ... import runtime_cache_version
from ...runtime.logging import log_debug, log_error, log_info, log_warning, should_log
from ...runtime.paths import (
    process_temp_dir,
    project_cache_dir,
)



def line_source_pdf_dir() -> Path:
    """Return this process's temporary PDF conversion directory."""
    return process_temp_dir() / "reply" / "line-source-pdf"


def line_source_docx_dir() -> Path:
    """Return this process's temporary line-source DOCX directory."""
    return process_temp_dir() / "reply" / "line-source-docx"


def line_source_cache_dir() -> Path:
    """Return the current project's reusable line-source cache."""
    return project_cache_dir() / "reply" / "line-source"


LINE_REGEX_PATTERN = re.compile(r"\(Line `([^`]+)`\)")
DOCX_CORE_PROPERTIES_PART = "docProps/core.xml"
DOCX_VOLATILE_CORE_PROPERTY_TAGS = {
    "{http://purl.org/dc/terms/}created",
    "{http://purl.org/dc/terms/}modified",
}


def hash_json_payload(payload: dict[str, Any]) -> str:
    """Return a stable digest for a JSON-serializable cache payload."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_docx_part(part_name: str, data: bytes) -> bytes:
    """Remove volatile package metadata that cannot affect rendered line layout."""
    if part_name != DOCX_CORE_PROPERTIES_PART:
        return data

    root = ElementTree.fromstring(data)
    for element in root.iter():
        if element.tag in DOCX_VOLATILE_CORE_PROPERTY_TAGS:
            # Pandoc writes the current build time here, which previously made
            # identical Markdown line sources miss the DOCX-to-PDF cache.
            element.text = ""
    return ElementTree.tostring(root, encoding="utf-8")


def docx_semantic_sha256(path: Path) -> str:
    """Hash DOCX part names and normalized contents without ZIP container timestamps."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        entries = sorted(
            (entry for entry in archive.infolist() if not entry.is_dir()),
            key=lambda entry: entry.filename,
        )
        for entry in entries:
            name = entry.filename.encode("utf-8")
            data = normalized_docx_part(entry.filename, archive.read(entry))
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()


def docx_dependency_record(path: Path) -> dict[str, str]:
    """Return a cache record based on layout-relevant DOCX package contents."""
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": docx_semantic_sha256(resolved),
    }


def line_source_pdf_backend() -> str:
    """Return the DOCX-to-PDF backend used on this platform."""
    return "word-com" if sys.platform == "win32" else "soffice"


def word_com_registration_status() -> bool | None:
    """Return whether Windows has a registered Microsoft Word COM ProgID."""
    if sys.platform != "win32":
        return True

    try:
        import winreg
    except ImportError:
        # A simulated or restricted Python runtime cannot inspect Windows' registry.
        return None

    flags = [getattr(winreg, "KEY_WOW64_64KEY", 0), 0]
    seen_flags: set[int] = set()
    for flag in flags:
        if flag in seen_flags:
            continue
        seen_flags.add(flag)
        try:
            with winreg.OpenKey(
                winreg.HKEY_CLASSES_ROOT,
                "Word.Application",
                0,
                winreg.KEY_READ | flag,
            ):
                return True
        except OSError:
            continue
    return False


def ensure_word_for_line_source(line_source: Path) -> None:
    """Explain the Windows Word requirement before converting non-PDF line sources."""
    if sys.platform != "win32" or line_source.suffix.lower() == ".pdf":
        return
    if word_com_registration_status() is not False:
        return
    raise RuntimeError(
        "Microsoft Word is required on Windows to resolve reply line numbers from "
        f"{line_source.suffix.lower() or 'this'} line sources, but Word was not detected. "
        "Please install Microsoft Word and retry `pmt build-reply`, or provide a PDF "
        "with `--manuscript-line-source`."
    )


def cached_line_source_path(kind: str, key: str, suffix: str) -> Path:
    """Return a persistent line-source cache path for a computed key."""
    return line_source_cache_dir() / kind / f"{key}{suffix}"


def work_line_source_path(directory: Path, stem: str, key: str, suffix: str) -> Path:
    """Return a hash-suffixed transient line-source work path."""
    return directory / f"{stem}.{key[:12]}{suffix}"


def copy_from_cache(cached: Path, target: Path, label: str) -> bool:
    """Copy a cached line-source artifact into the work directory if present."""
    if not cached.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, target)
    log_debug(f"[LINE] Reusing cached {label}: {target}")
    return True


def store_in_cache(source: Path, cached: Path, label: str) -> None:
    """Store a generated line-source artifact in the persistent cache."""
    cached.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, cached)
    log_debug(f"[LINE] Cached {label}: {cached}")


def docx_line_source_pdf_cache_key(source_docx: Path) -> str:
    """Return a fingerprint for converting a DOCX line source to PDF."""
    return hash_json_payload(
        {
            "kind": "docx-line-source-pdf",
            "schema": 2,
            "pmt_version": runtime_cache_version(),
            "backend": line_source_pdf_backend(),
            "source": docx_dependency_record(source_docx),
        }
    )


def prepare_cached_docx_line_source_pdf(source_docx: Path) -> Path:
    """Convert or reuse the cached PDF for a DOCX line source."""
    key = docx_line_source_pdf_cache_key(source_docx)
    cached_pdf = cached_line_source_path("pdf", key, ".pdf")
    target_pdf = work_line_source_path(line_source_pdf_dir(), source_docx.stem, key, ".pdf")
    if copy_from_cache(cached_pdf, target_pdf, "line-source PDF"):
        return target_pdf

    ensure_word_for_line_source(source_docx)
    if sys.platform == "win32":
        export_docx_to_pdf_with_word(source_docx, target_pdf)
    else:
        export_docx_to_pdf_with_soffice(source_docx, target_pdf)
    store_in_cache(target_pdf, cached_pdf, "line-source PDF")
    return target_pdf


def normalized_pdf_line_text(text: str) -> str:
    """Normalize one PDF text line so regexes can survive extraction artifacts."""
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def pdf_metadata_source(metadata: dict[str, str] | None) -> str:
    """Join PDF metadata fields for producer-specific extraction decisions."""
    if not metadata:
        return ""
    return " ".join(str(value) for value in metadata.values()).lower()


def is_libreoffice_pdf(metadata: dict[str, str] | None) -> bool:
    """Return whether PDF metadata identifies LibreOffice as the producer."""
    return "libreoffice" in pdf_metadata_source(metadata)


def is_microsoft_word_pdf(metadata: dict[str, str] | None) -> bool:
    """Return whether PDF metadata identifies Microsoft Word as the producer."""
    source = pdf_metadata_source(metadata)
    return "microsoft" in source and "word" in source


def extract_pdf_numbered_lines_by_text_order(document: Any) -> list[tuple[int, int, str]]:
    """Extract line-number pairs from PDFs whose text stream interleaves text then number."""
    numbered_lines: list[tuple[int, int, str]] = []
    for page_index, page in enumerate(document, start=1):
        lines = page.get_text("text").splitlines()
        i = 0
        while i < len(lines) - 1:
            text = normalized_pdf_line_text(lines[i])
            maybe_number = lines[i + 1].strip()
            if text and re.fullmatch(r"\d+", maybe_number):
                numbered_lines.append((int(maybe_number), page_index, text))
                i += 2
                continue
            i += 1
    return numbered_lines


def y_center(bbox: tuple[float, float, float, float]) -> float:
    """Return a text-line bounding box's vertical center for layout matching."""
    return (bbox[1] + bbox[3]) / 2


def group_body_lines_by_y(
    text_lines: list[tuple[str, tuple[float, float, float, float]]],
) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Keep one body text line per y position, avoiding isolated superscript fragments."""
    groups: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    for text, bbox in text_lines:
        center = y_center(bbox)
        for group in groups:
            if abs(y_center(group[0][1]) - center) <= 1.0:
                group.append((text, bbox))
                break
        else:
            groups.append([(text, bbox)])

    body_lines: list[tuple[str, tuple[float, float, float, float]]] = []
    for group in groups:
        body_lines.append(max(group, key=lambda item: len(item[0])))
    return body_lines


def keep_increasing_line_numbers(
    numbered_lines: list[tuple[int, int, str]],
) -> list[tuple[int, int, str]]:
    """Drop LibreOffice footnote line-number resets that appear after body line numbers."""
    kept: list[tuple[int, int, str]] = []
    last_line_number = 0
    for item in numbered_lines:
        line_number, _, _ = item
        if line_number <= last_line_number:
            log_debug(f"[LINE] Skipping non-increasing LibreOffice line number: {line_number}")
            continue
        kept.append(item)
        last_line_number = line_number
    return kept


def extract_pdf_numbered_lines_by_layout(document: Any) -> list[tuple[int, int, str]]:
    """Pair left-margin line numbers with body text by y coordinate for layout PDFs."""
    numbered_lines: list[tuple[int, int, str]] = []
    for page_index, page in enumerate(document, start=1):
        number_lines: list[tuple[int, tuple[float, float, float, float]]] = []
        text_lines: list[tuple[str, tuple[float, float, float, float]]] = []

        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                bbox = line.get("bbox")
                if not bbox:
                    continue
                text = normalized_pdf_line_text(
                    "".join(str(span.get("text", "")) for span in line.get("spans", []))
                )
                if not text:
                    continue
                line_bbox = tuple(float(value) for value in bbox)
                if re.fullmatch(r"\d+", text):
                    number_lines.append((int(text), line_bbox))
                else:
                    text_lines.append((text, line_bbox))

        if not number_lines or not text_lines:
            continue

        body_left = min(bbox[0] for _, bbox in text_lines)
        margin_numbers = [(number, bbox) for number, bbox in number_lines if bbox[0] < body_left - 2]
        body_lines = group_body_lines_by_y(text_lines)

        for number, number_bbox in margin_numbers:
            number_y = y_center(number_bbox)
            matches = [
                (text, bbox)
                for text, bbox in body_lines
                if abs(y_center(bbox) - number_y) <= max(3.0, (number_bbox[3] - number_bbox[1]) * 0.75)
            ]
            if not matches:
                continue
            text, _ = max(matches, key=lambda item: len(item[0]))
            numbered_lines.append((number, page_index, text))
    return keep_increasing_line_numbers(numbered_lines)


def export_docx_to_pdf_with_word(source_docx: Path, target_pdf: Path) -> None:
    """Export a DOCX line source to PDF through Microsoft Word COM automation."""
    if sys.platform != "win32":
        raise RuntimeError("DOCX line-source conversion requires Microsoft Word COM on Windows.")

    source_docx = source_docx.resolve()
    target_pdf = target_pdf.resolve()
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    target_pdf.unlink(missing_ok=True)

    log_info(f"[LINE] Converting DOCX line source to PDF with Word COM: {source_docx}")
    powershell = r"""
param([string]$source, [string]$target)
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($source, $false, $true, $false)
    $document.SaveAs2($target, 17)
} finally {
    if ($null -ne $document) {
        $document.Close($false) | Out-Null
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) | Out-Null
    }
    if ($null -ne $word) {
        $word.Quit() | Out-Null
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""
    script_path = line_source_pdf_dir() / "word_docx_to_pdf.ps1"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(powershell, encoding="utf-8")
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            str(source_docx),
            str(target_pdf),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        if result.stdout.strip():
            log_error(result.stdout.strip())
        if result.stderr.strip():
            log_error(result.stderr.strip())
        raise RuntimeError(f"Word COM DOCX-to-PDF conversion failed: {source_docx}")
    if not target_pdf.exists():
        raise RuntimeError(f"Word COM conversion did not create PDF: {target_pdf}")
    log_info(f"[LINE] Word COM PDF created: {target_pdf}")


def export_docx_to_pdf_with_soffice(source_docx: Path, target_pdf: Path) -> None:
    """Export a DOCX line source to PDF through LibreOffice's soffice CLI."""
    source_docx = source_docx.resolve()
    target_pdf = target_pdf.resolve()
    target_pdf.parent.mkdir(parents=True, exist_ok=True)

    expected_pdf = target_pdf.parent / f"{source_docx.stem}.pdf"
    target_pdf.unlink(missing_ok=True)
    if expected_pdf != target_pdf:
        expected_pdf.unlink(missing_ok=True)

    log_info(f"[LINE] Converting DOCX line source to PDF with soffice: {source_docx}")
    result = subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(target_pdf.parent),
            str(source_docx),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        if result.stdout.strip():
            log_error(result.stdout.strip())
        if result.stderr.strip():
            log_error(result.stderr.strip())
        raise RuntimeError(f"soffice DOCX-to-PDF conversion failed: {source_docx}")
    if not expected_pdf.exists():
        raise RuntimeError(f"soffice conversion did not create PDF: {expected_pdf}")
    if expected_pdf != target_pdf:
        shutil.move(str(expected_pdf), str(target_pdf))
    log_info(f"[LINE] soffice PDF created: {target_pdf}")


def build_markdown_line_source_docx(source_markdown: Path, target_docx: Path) -> None:
    """Build a Markdown line source to DOCX before converting it to PDF."""
    from .. import build as manuscript_build

    source_markdown = source_markdown.resolve()
    target_docx = target_docx.resolve()
    target_docx.parent.mkdir(parents=True, exist_ok=True)

    previous_settings = manuscript_build.SETTINGS.model_copy(deep=True)
    quiet = not should_log("DEBUG")
    captured_stdout = StringIO()
    captured_stderr = StringIO()
    result = 1

    def replay_captured_output() -> None:
        """Replay nested build diagnostics only when the hidden build fails."""
        for buffer, target in (
            (captured_stdout, sys.stdout),
            (captured_stderr, sys.stderr),
        ):
            output = buffer.getvalue()
            if output:
                print(output, end="" if output.endswith("\n") else "\n", file=target)

    try:
        log_info(f"[LINE] Building Markdown line source DOCX: {source_markdown}")
        try:
            with ExitStack() as stack:
                if quiet:
                    # The line-source DOCX is an internal intermediate build;
                    # keep its full pipeline output for failures or --verbose.
                    stack.enter_context(redirect_stdout(captured_stdout))
                    stack.enter_context(redirect_stderr(captured_stderr))
                result = manuscript_build.run_build_command(
                    target="docx",
                    markdown=str(source_markdown),
                    output_file=str(target_docx),
                    warn_hat_order=False,
                )
        except BaseException:
            if quiet:
                replay_captured_output()
            raise
    finally:
        for key, value in previous_settings.model_dump().items():
            setattr(manuscript_build.SETTINGS, key, value)

    if result != 0:
        if quiet:
            replay_captured_output()
        raise RuntimeError(f"Markdown line-source DOCX build failed: {source_markdown}")
    if not target_docx.exists():
        raise RuntimeError(f"Markdown line-source DOCX build did not create: {target_docx}")
    log_debug(f"[LINE] Markdown line source DOCX created: {target_docx}")


def prepare_line_source_pdf(line_source: Path) -> Path:
    """Return a PDF path for line-regex matching, converting DOCX sources if needed."""
    if not line_source.exists():
        raise FileNotFoundError(f"Line source file not found: {line_source}")

    suffix = line_source.suffix.lower()
    if suffix == ".pdf":
        return line_source
    if suffix in {".md", ".markdown"}:
        target_docx = line_source_docx_dir() / f"{line_source.stem}.docx"
        build_markdown_line_source_docx(line_source, target_docx)
        line_source = target_docx
        suffix = line_source.suffix.lower()
    if suffix in {".docx", ".docm"}:
        return prepare_cached_docx_line_source_pdf(line_source)
    raise RuntimeError(f"Line source must be a Markdown, PDF, or Word document: {line_source}")


def extract_pdf_numbered_lines(pdf: Path) -> list[tuple[int, int, str]]:
    """Extract manuscript line numbers and their corresponding text from a PDF."""
    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required to resolve reply line regexes.") from exc

    if not pdf.exists():
        raise FileNotFoundError(f"Manuscript PDF not found for line resolution: {pdf}")

    with pymupdf.open(pdf) as document:
        if is_libreoffice_pdf(document.metadata):
            numbered_lines = extract_pdf_numbered_lines_by_layout(document)
            log_debug(
                f"[DEBUG] Extracted {len(numbered_lines)} numbered PDF text lines from {pdf} using LibreOffice layout matching."
            )
            return numbered_lines

        if is_microsoft_word_pdf(document.metadata):
            numbered_lines = extract_pdf_numbered_lines_by_layout(document)
            if numbered_lines:
                log_debug(
                    f"[DEBUG] Extracted {len(numbered_lines)} numbered PDF text lines from {pdf} using Word layout matching."
                )
                return numbered_lines
            log_warning("[WARN] Microsoft Word PDF layout matching found no numbered lines; falling back to text order.")

        numbered_lines = extract_pdf_numbered_lines_by_text_order(document)

    log_debug(f"[DEBUG] Extracted {len(numbered_lines)} numbered PDF text lines from {pdf}.")
    return numbered_lines


def build_pdf_search_text(
    numbered_lines: list[tuple[int, int, str]],
) -> tuple[str, list[tuple[int, int, int]]]:
    """Join numbered PDF lines and keep offsets for mapping regex hits to line numbers."""
    parts: list[str] = []
    offsets: list[tuple[int, int, int]] = []
    cursor = 0
    for line_number, page_number, text in numbered_lines:
        offsets.append((cursor, line_number, page_number))
        parts.append(text)
        cursor += len(text) + 1
    return " ".join(parts), offsets


def line_number_for_offset(offsets: list[tuple[int, int, int]], position: int) -> tuple[int, int]:
    """Return the PDF line number and page containing a joined-text character offset."""
    current_line = 0
    current_page = 0
    for offset, line_number, page_number in offsets:
        if offset > position:
            break
        current_line = line_number
        current_page = page_number
    return current_line, current_page


def resolve_line_regexes(markdown: str, line_source: Path) -> str:
    """Replace (Line `regex`) placeholders with unique line numbers from PDF/DOCX source."""
    patterns = sorted(set(LINE_REGEX_PATTERN.findall(markdown)))
    if not patterns:
        return markdown

    line_source_pdf = prepare_line_source_pdf(line_source)
    search_text, offsets = build_pdf_search_text(extract_pdf_numbered_lines(line_source_pdf))
    replacements: dict[str, str] = {}

    for pattern in patterns:
        try:
            regex = re.compile(pattern, flags=re.IGNORECASE)
        except re.error as exc:
            log_warning(f"[LINE] Invalid regex in reply line placeholder: `{pattern}` ({exc})")
            continue

        matches = list(regex.finditer(search_text))
        if len(matches) != 1:
            log_warning(f"[LINE] Regex `{pattern}` matched {len(matches)} PDF locations; leaving placeholder unchanged.")
            continue

        line_number, page_number = line_number_for_offset(offsets, matches[0].start())
        replacements[pattern] = f"(Line {line_number})"
        log_debug(f"[LINE] `{pattern}` -> Line {line_number} (PDF page {page_number})")

    def replace_match(match: re.Match[str]) -> str:
        """Return resolved line text, preserving unresolved regex placeholders."""
        return replacements.get(match.group(1), match.group(0))

    resolved = LINE_REGEX_PATTERN.sub(replace_match, markdown)
    log_debug(f"[DEBUG] Resolved {len(replacements)} of {len(patterns)} unique line regexes.")
    return resolved
