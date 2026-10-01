"""Reuse HTML metadata preparation while observing source and style changes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..runtime.metadata import bundled_style_path, load_effective_metadata, write_pandoc_metadata
from ..runtime.resources import template_root
from .build import prepare_html_metadata


class ProjectMetadataCache:
    """Cache expensive YAML/CSS work by header and style dependencies, never prose."""

    _HEADER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", re.DOTALL)

    def __init__(self, config: dict[str, Any], work_dir: Path) -> None:
        """Keep project settings and a per-source metadata preparation cache."""
        self._config = config
        self._work_dir = work_dir
        self._entries: dict[Path, tuple[object, dict[str, Any], Path, dict[str, str | None]]] = {}
        self._context = config.get("metadata_sources")

    @staticmethod
    def _marker(path: Path) -> tuple[str, int, int, int]:
        """Observe creation, edits, and deletion without parsing unchanged files."""
        try:
            stat = path.stat()
            return str(path), stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns
        except FileNotFoundError:
            return str(path), -1, -1, -1

    def style_paths(self, source: Path) -> tuple[Path, ...]:
        """Match CLI discovery, including a style file created after server startup."""
        if not self._context:
            return ()
        style_file = Path(self._context.get("style_file", "style.yml"))
        return tuple(dict.fromkeys((source.parent / style_file, Path(self._config["project_dir"]) / style_file)))

    def refresh(self, source: Path, text: str) -> tuple[dict[str, Any], Path | None, str, dict[str, str | None]]:
        """Refresh changed YAML/style inputs and return the header-free manuscript."""
        if not self._context:
            # Older project configurations keep their original metadata policy.
            return self._config.get("pandoc_metadata", {}), None, text, {}
        match = self._HEADER.match(text)
        header = match.group(0) if match else ""
        paths = self.style_paths(source)
        dependencies = (*paths, bundled_style_path(False), bundled_style_path(True),
                        template_root() / "pandoc/manuscript-template/reference-doc/word/styles.xml")
        signature = (header, tuple(self._marker(path) for path in dependencies))
        cached = self._entries.get(source)
        if cached is None or cached[0] != signature:
            from ..commands.build import pandoc_filter_env

            roots = [source.parent, Path(self._config["project_dir"]),
                     *(Path(path) for path in self._config.get("resource_paths", [])), template_root()]
            self._work_dir.mkdir(parents=True, exist_ok=True)
            index = len(self._entries) if cached is None else list(self._entries).index(source)
            header_path = self._work_dir / f"header-{index}.md"
            # Parse the header from the same immutable source snapshot as the
            # body; an editor save during YAML loading must not mix versions.
            header_path.write_text(header, encoding="utf-8")
            effective = load_effective_metadata(
                header_path, style_paths=[path for path in paths if path.is_file()], allow_missing_header=True,
                # Pandoc interprets YAML strings as Markdown; forward slashes
                # prevent Windows backslashes from escaping CSL path characters.
                csl_resolver=lambda raw: next((root / raw for root in roots if (root / raw).is_file()), roots[-1] / raw).as_posix(),
            )
            prepare_html_metadata(effective, self._context.get("project_name", source.stem))
            # A server request must also clear an old custom delimiter when a
            # style edit restores the default; the native process is persistent.
            environment: dict[str, str | None] = {"PMT_CITATION_NUMBER_RANGE_DELIMITER": None}
            environment.update(pandoc_filter_env(effective.pmt_settings))
            self._work_dir.mkdir(parents=True, exist_ok=True)
            target = self._work_dir / f"metadata-{len(self._entries)}.yml" if cached is None else cached[2]
            write_pandoc_metadata(effective.pandoc_metadata, target)
            cached = (signature, effective.pandoc_metadata, target, environment)
            self._entries[source] = cached
        return cached[1], cached[2], text[len(header):], cached[3]
