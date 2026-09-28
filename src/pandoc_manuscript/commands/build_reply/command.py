"""Thin command entry points for `papper build-reply`."""

from __future__ import annotations

from pathlib import Path

from ...runtime.logging import log_error, log_info, log_warning
from .output import (
    build_reply_docx,
    build_reply_txt,
    reply_output_format,
    reply_output_path,
    reply_reference_doc_path,
)
from .settings import (
    DEFAULT_REPLY_FROM_FORMAT,
    DEFAULT_REPLY_LINE_SOURCE,
    DEFAULT_REPLY_MANUSCRIPT_FILE,
)
from .resolve import style_paths_for_markdown


def checked_markdown_path(markdown_path: str | Path) -> Path:
    """Return an existing reply markdown path, raising clear input errors."""
    path = Path(markdown_path)
    if not path.exists():
        raise FileNotFoundError(f"Reply markdown file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Reply markdown path is not a file: {path}")
    return path


def resolve_reply_companion(reply: Path, value: str | None, default: str) -> Path:
    """Resolve reply companion files beside the reply before checking cwd."""
    candidate = Path(value or default)
    if candidate.is_absolute():
        return candidate
    for root in (reply.resolve().parent, Path.cwd().resolve()):
        resolved = root / candidate
        if resolved.exists():
            return resolved
    return candidate


def run_build_reply_command(
    *,
    markdown: str,
    reply_manuscript: str | None = None,
    manuscript_line_source: str | None = None,
    from_format: str | None = None,
    reference_doc: str | None = None,
    output_file: str | None = None,
) -> int:
    """Apply parsed `papper build-reply` settings and run the reply build."""
    reply = checked_markdown_path(markdown)

    try:
        output = reply_output_path(reply, output_file)
        output_format = reply_output_format(output)
        style_paths = style_paths_for_markdown(reply)
        manuscript = resolve_reply_companion(reply, reply_manuscript, DEFAULT_REPLY_MANUSCRIPT_FILE)
        line_source = resolve_reply_companion(reply, manuscript_line_source, DEFAULT_REPLY_LINE_SOURCE)
        active_from_format = from_format or DEFAULT_REPLY_FROM_FORMAT
        if output_format == "txt":
            log_info("\n[TXT] Building reviewer reply TXT...\n")
            build_reply_txt(
                reply=reply,
                manuscript=manuscript,
                manuscript_line_source=line_source,
                output=output,
                style=style_paths,
                from_format=active_from_format,
            )
        else:
            log_info("[DOCX] Building reviewer reply DOCX...")
            build_reply_docx(
                reply=reply,
                manuscript=manuscript,
                manuscript_line_source=line_source,
                output=output,
                reference_doc=reply_reference_doc_path(reference_doc),
                style=style_paths,
                from_format=active_from_format,
            )
        return 0
    except KeyboardInterrupt:
        log_warning("\n\n[WARN] Build interrupted by user.")
        return 1
    except Exception as exc:
        log_error(f"\n[ERROR] {exc}")
        return 1
