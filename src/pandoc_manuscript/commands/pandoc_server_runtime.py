"""Run the project-bound PMT HTML server used by editor integrations.

The process keeps one Haskell Pandoc worker alive, loads the same defaults and
metadata that `pmt build html` uses, and exposes only project-aware requests.
Clients send a Markdown path; they do not need to know PMT filters, templates,
CSL files, resource paths, or other Pandoc options. The Python layer applies
the same HTML post-processing as the normal build before returning HTML.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict
import yaml

from ..html.postprocess import postprocess_html_text
from ..runtime.paths import PMT_DIR, PMT_TOOLS_BIN_DIR


class ServerRuntimeSettings(BaseSettings):
    """CLI settings for the internal PMT HTTP runtime."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    config: Path = Field(description="Project-bound PMT server configuration JSON.")
    host: str = Field(default="127.0.0.1", description="HTTP bind host.")
    port: int = Field(default=3030, description="HTTP port.")


@dataclass(frozen=True)
class ConversionResult:
    """HTML plus timing and cache information for one server request."""

    html: str
    timings: dict[str, float]
    cache_hit: bool
    mode: str


class ProjectAssetCache:
    """Cache project metadata assets and produce a dependency fingerprint."""

    _PATH_OPTIONS = {
        "--defaults",
        "--metadata-file",
        "--csl",
        "--bibliography",
        "--template",
        "--include-in-header",
        "--include-before-body",
        "--include-after-body",
        "--resource-path",
    }
    _REFERENCE_PATTERN = re.compile(
        r"!\[[^\]]*\]\(([^)\s]+)|(?:src|href)=[\"']([^\"']+)[\"']",
        re.IGNORECASE,
    )

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._entries: dict[str, tuple[int, int, str]] = {}
        self._cached_bytes: dict[str, bytes] = {}
        self._defaults_cache: dict[str, tuple[int, int, dict[str, Any]]] = {}
        self._last_paths: tuple[Path, ...] = ()

    @staticmethod
    def _resolve(raw: object, base: Path) -> Path | None:
        """Resolve a configured path while ignoring non-file metadata values."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        value = raw.strip().replace("${.}", str(base))
        path = Path(value)
        return path if path.is_absolute() else (base / path).resolve()

    def _paths(self) -> tuple[Path, ...]:
        """Collect defaults, templates, CSL, bibliography, and filter files."""
        project_dir = Path(self._config["project_dir"]).resolve()
        resource_roots = tuple(
            Path(item).resolve()
            for item in self._config.get("resource_paths", [])
            if isinstance(item, str) and item.strip()
        )
        if not resource_roots:
            resource_roots = (project_dir,)
        args = [str(item) for item in self._config.get("pandoc_args", [])]
        values: list[Path] = []
        defaults_files: list[Path] = []
        for index, arg in enumerate(args):
            if arg in self._PATH_OPTIONS and index + 1 < len(args):
                resolved = self._resolve(args[index + 1], project_dir)
                if resolved is not None:
                    values.append(resolved)
                    if arg == "--defaults":
                        defaults_files.append(resolved)

        metadata = self._config.get("pandoc_metadata", {})
        if isinstance(metadata, dict):
            for key in ("csl", "bibliography", "template", "reference-doc"):
                raw = metadata.get(key)
                raw_values = raw if isinstance(raw, list) else [raw]
                for item in raw_values:
                    resolved = self._resolve(item, project_dir)
                    if resolved is not None:
                        values.append(resolved)
        values.extend(resource_roots)

        # Defaults files name filters/templates relative to their own directory.
        for defaults_path in defaults_files:
            try:
                stat = defaults_path.stat()
                marker = (stat.st_mtime_ns, stat.st_size)
                cached_defaults = self._defaults_cache.get(str(defaults_path))
                if cached_defaults is not None and cached_defaults[:2] == marker:
                    defaults = cached_defaults[2]
                else:
                    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8")) or {}
                    if isinstance(defaults, dict):
                        self._defaults_cache[str(defaults_path)] = (*marker, defaults)
            except (OSError, UnicodeError, yaml.YAMLError):
                continue
            if not isinstance(defaults, dict):
                continue
            for key in ("template", "csl", "bibliography", "metadata-file", "metadata-files", "filters"):
                raw = defaults.get(key)
                raw_values = raw if isinstance(raw, list) else [raw]
                for item in raw_values:
                    resolved = self._resolve(item, defaults_path.parent)
                    if resolved is not None:
                        values.append(resolved)
        return tuple(sorted(set(values), key=lambda item: str(item).casefold()))

    def refresh(self) -> str:
        """Read changed assets once and return their stable dependency digest."""
        paths = self._paths()
        self._last_paths = paths
        digest = hashlib.sha256()
        for path in paths:
            key = str(path)
            try:
                stat = path.stat()
                marker = (stat.st_mtime_ns, stat.st_size)
                previous = self._entries.get(key)
                if previous is not None and previous[:2] == marker:
                    content_digest = previous[2]
                else:
                    content = path.read_bytes()
                    content_digest = hashlib.sha256(content).hexdigest()
                    self._cached_bytes[key] = content
                    self._entries[key] = (*marker, content_digest)
                digest.update(key.encode("utf-8"))
                digest.update(content_digest.encode("ascii"))
            except OSError:
                digest.update(key.encode("utf-8"))
                digest.update(b"<missing>")
        metadata_blob = json.dumps(
            self._config.get("pandoc_metadata", {}),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        digest.update(metadata_blob)
        return digest.hexdigest()

    def source_digest(self, path: Path) -> str:
        """Hash the manuscript and referenced local resources for safe reuse."""
        key = str(path)
        stat = path.stat()
        marker = (stat.st_mtime_ns, stat.st_size)
        previous = self._entries.get(key)
        if previous is not None and previous[:2] == marker:
            content = self._cached_bytes.get(key, b"")
            content_digest = previous[2]
        else:
            content = path.read_bytes()
            content_digest = hashlib.sha256(content).hexdigest()
            self._cached_bytes[key] = content
            self._entries[key] = (*marker, content_digest)
        digest = hashlib.sha256(content_digest.encode("ascii"))
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        base = path.parent
        for match in self._REFERENCE_PATTERN.finditer(text):
            raw = next((value for value in match.groups() if value), "")
            if not raw or raw.startswith(("#", "http:", "https:", "data:", "mailto:")):
                continue
            referenced = Path(raw.split("#", 1)[0].split("?", 1)[0])
            if not referenced.is_absolute():
                referenced = (base / referenced).resolve()
            try:
                ref_stat = referenced.stat()
                ref_key = str(referenced)
                ref_marker = (ref_stat.st_mtime_ns, ref_stat.st_size)
                previous_ref = self._entries.get(ref_key)
                if previous_ref is not None and previous_ref[:2] == ref_marker:
                    ref_digest = previous_ref[2]
                else:
                    ref_digest = hashlib.sha256(referenced.read_bytes()).hexdigest()
                    self._entries[ref_key] = (*ref_marker, ref_digest)
                digest.update(ref_key.encode("utf-8"))
                digest.update(ref_digest.encode("ascii"))
            except OSError:
                digest.update(str(referenced).encode("utf-8"))
                digest.update(b"<missing>")
        return digest.hexdigest()

    @property
    def asset_count(self) -> int:
        """Return the number of project files currently held by the cache."""
        return len(self._last_paths)


class PandocWorker:
    """Serialize requests to one long-lived Pandoc API worker process."""

    def __init__(self, config: dict[str, Any], log_path: Path) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._assets = ProjectAssetCache(config)
        self._result_cache: OrderedDict[str, str] = OrderedDict()
        self._result_cache_limit = 8
        self._ast_cache_dir = PMT_DIR / "work" / "pandoc-server-ast"
        self._ast_lock = threading.Lock()
        self._ast_building: set[str] = set()
        self._process = self._start_process(config, log_path)
        self._assets.refresh()

    @staticmethod
    def _resolve_command(config: dict[str, Any]) -> list[str]:
        """Resolve the worker executable configured for the current project."""
        configured = os.environ.get("PMT_PANDOC_SERVER_WORKER_COMMAND")
        if configured:
            import shlex

            return shlex.split(configured, posix=os.name != "nt")

        names = ("pmt-pandoc-worker.exe", "pmt-pandoc-worker") if os.name == "nt" else ("pmt-pandoc-worker",)
        for name in names:
            candidate = PMT_TOOLS_BIN_DIR / name
            if candidate.exists():
                return [str(candidate)]
        raise FileNotFoundError(
            "PMT Pandoc worker not found. Build scripts/pandoc-server and install "
            "pmt-pandoc-worker into .pmt/tools/bin, or set PMT_PANDOC_SERVER_WORKER_COMMAND."
        )

    @classmethod
    def _start_process(cls, config: dict[str, Any], log_path: Path) -> subprocess.Popen[str]:
        """Start the worker with the fixed project Pandoc options."""
        command = [*cls._resolve_command(config), "--config", str(Path(config["worker_config"]).resolve())]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_handle,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=config["project_dir"],
                env=os.environ.copy(),
                bufsize=1,
            )
        except BaseException:
            log_handle.close()
            raise
        log_handle.close()
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise RuntimeError("Could not open the PMT Pandoc worker protocol streams.")
        return process

    def _request(
        self,
        source: Path,
        output: Path,
        *,
        mode: str,
        input_format: str | None = None,
    ) -> dict[str, Any]:
        """Send one serialized conversion request to the persistent worker."""
        request: dict[str, object] = {
            "input": str(source),
            "output": str(output),
            "mode": mode,
        }
        if input_format is not None:
            request["inputFormat"] = input_format
        with self._lock:
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            response = self._process.stdout.readline()
        if not response:
            raise RuntimeError("PMT Pandoc worker exited without a response.")
        result = json.loads(response)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error", "PMT Pandoc worker conversion failed.")))
        return result

    def convert(self, input_path: Path, *, mode: str = "exact") -> ConversionResult:
        """Convert one project Markdown file with exact or fast-preview semantics."""
        started = time.perf_counter()
        source = input_path.resolve()
        asset_started = time.perf_counter()
        asset_digest = self._assets.refresh()
        source_digest = self._assets.source_digest(source)
        timings: dict[str, float] = {
            "asset_cache": round((time.perf_counter() - asset_started) * 1000, 3),
        }
        cache_key = hashlib.sha256(
            f"{mode}:{source}:{source_digest}:{asset_digest}".encode("utf-8")
        ).hexdigest()
        with self._cache_lock:
            cached = self._result_cache.get(cache_key)
            if cached is not None:
                self._result_cache.move_to_end(cache_key)
        if cached is not None:
            timings["cache_lookup"] = round((time.perf_counter() - started) * 1000, 3)
            return ConversionResult(cached, timings, True, mode)

        output_dir = PMT_DIR / "work" / "pandoc-server-output"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._ast_cache_dir.mkdir(parents=True, exist_ok=True)
        ast_path = self._ast_cache_dir / f"{cache_key}.json"
        try:
            if mode == "preview":
                ast_started = time.perf_counter()
                sections = self._split_sections(source)
                with tempfile.NamedTemporaryFile(prefix="html-", suffix=".html", dir=output_dir, delete=False) as handle:
                    output_path = Path(handle.name)
                if sections is not None and self._has_section_cache(sections, asset_digest):
                    self._combine_section_ast(sections, asset_digest, ast_path)
                    timings["ast_reuse"] = round((time.perf_counter() - ast_started) * 1000, 3)
                    worker_result = self._request(ast_path, output_path, mode="preview", input_format="json")
                else:
                    timings["ast_cache_miss"] = 1.0
                    worker_result = self._request(source, output_path, mode="preview")
                    if sections is not None:
                        self._schedule_ast_build(sections, asset_digest, cache_key)
            else:
                with tempfile.NamedTemporaryFile(prefix="html-", suffix=".html", dir=output_dir, delete=False) as handle:
                    output_path = Path(handle.name)
                worker_result = self._request(source, output_path, mode="exact")
            timings["worker"] = float(worker_result.get("elapsed_ms", 0.0))
            read_started = time.perf_counter()
            raw_html = output_path.read_text(encoding="utf-8")
            timings["read_output"] = round((time.perf_counter() - read_started) * 1000, 3)
            post_started = time.perf_counter()
            html = postprocess_html_text(
                raw_html,
                pandoc_metadata=self._config.get("pandoc_metadata", {}),
                skip_author_info=mode == "preview",
            )
            timings["postprocess"] = round((time.perf_counter() - post_started) * 1000, 3)
            with self._cache_lock:
                self._result_cache[cache_key] = html
                self._result_cache.move_to_end(cache_key)
                while len(self._result_cache) > self._result_cache_limit:
                    self._result_cache.popitem(last=False)
            timings["total"] = round((time.perf_counter() - started) * 1000, 3)
            return ConversionResult(html, timings, False, mode)
        finally:
            if "output_path" in locals():
                output_path.unlink(missing_ok=True)

    @staticmethod
    def _split_sections(source: Path) -> list[str] | None:
        """Split at top-level ATX headings when no cross-section definitions exist."""
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        if any(re.match(r"^ {0,3}\[(?:\^)?[^]]+\]:", line) for line in lines):
            return None
        boundaries = [0]
        fence: str | None = None
        for index, line in enumerate(lines):
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening:
                marker = opening.group(1)
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence):
                    fence = None
            if fence is None and index and re.match(r"^#\s+", line):
                boundaries.append(index)
        if fence is not None or len(boundaries) < 3 or len(boundaries) > 32:
            return None
        boundaries.append(len(lines))
        return ["".join(lines[start:end]) for start, end in zip(boundaries, boundaries[1:])]

    def _section_path(self, section: str, asset_digest: str) -> Path:
        """Use content addressing so unchanged sections survive nearby edits."""
        digest = hashlib.sha256((asset_digest + section).encode("utf-8")).hexdigest()
        return self._ast_cache_dir / f"section-{digest}.json"

    def _has_section_cache(self, sections: list[str], asset_digest: str) -> bool:
        """Return whether at least one section can be reused."""
        return any(self._section_path(section, asset_digest).exists() for section in sections)

    def _combine_section_ast(self, sections: list[str], asset_digest: str, target: Path) -> None:
        """Parse changed sections, then merge reader ASTs before global filters."""
        combined: dict[str, Any] | None = None
        blocks: list[object] = []
        for section in sections:
            section_path = self._section_path(section, asset_digest)
            if not section_path.exists():
                self._build_section_ast(section, section_path)
            document = json.loads(section_path.read_text(encoding="utf-8"))
            if combined is None:
                combined = document
            blocks.extend(document["blocks"])
        assert combined is not None
        combined["blocks"] = blocks
        target.write_text(json.dumps(combined, ensure_ascii=False), encoding="utf-8")

    def _build_section_ast(self, section: str, target: Path) -> None:
        """Build a single section AST using Pandoc's reader options."""
        with tempfile.NamedTemporaryFile(
            prefix="section-", suffix=".md", dir=self._ast_cache_dir, delete=False, mode="w", encoding="utf-8"
        ) as handle:
            handle.write(section)
            section_source = Path(handle.name)
        temporary = target.with_suffix(".tmp")
        try:
            self._request(section_source, temporary, mode="ast")
            temporary.replace(target)
        finally:
            section_source.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

    def _schedule_ast_build(self, sections: list[str], asset_digest: str, cache_key: str) -> None:
        """Warm unchanged sections after the first full preview response."""
        with self._ast_lock:
            if cache_key in self._ast_building:
                return
            self._ast_building.add(cache_key)

        def build() -> None:
            try:
                for section in sections:
                    path = self._section_path(section, asset_digest)
                    if not path.exists():
                        self._build_section_ast(section, path)
            except (OSError, RuntimeError, ValueError):
                pass
            finally:
                with self._ast_lock:
                    self._ast_building.discard(cache_key)

        threading.Thread(target=build, name="pmt-pandoc-ast-cache", daemon=True).start()

    def close(self) -> None:
        """Stop the worker when the HTTP runtime shuts down."""
        if self._process.poll() is None:
            self._process.terminate()
            self._process.wait(timeout=3)

    def is_alive(self) -> bool:
        """Return whether the long-lived Pandoc worker is still running."""
        return self._process.poll() is None

    def cache_stats(self) -> dict[str, int]:
        """Return lightweight cache counters for diagnostics."""
        with self._cache_lock:
            return {"html_entries": len(self._result_cache), "asset_files": self._assets.asset_count}


class PmtHtmlRequestHandler(BaseHTTPRequestHandler):
    """Serve project-aware HTML conversion and health endpoints."""

    server_version = "PMT-Pandoc-Server/0.1"

    def _json_response(self, payload: object, status: int = HTTPStatus.OK) -> None:
        """Write one UTF-8 JSON response."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, result: ConversionResult, *, status: int = HTTPStatus.OK) -> None:
        """Write processed HTML directly, avoiding a JSON response envelope."""
        body = result.html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-PMT-Cache", "hit" if result.cache_hit else "miss")
        self.send_header("X-PMT-Mode", result.mode)
        self.send_header(
            "Server-Timing",
            ", ".join(f"{name};dur={value:.3f}" for name, value in result.timings.items()),
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Handle the health and version endpoint."""
        if self.path == "/version":
            worker = getattr(self.server, "worker", None)
            if worker is None or not worker.is_alive():
                self._json_response({"error": "Pandoc worker is not running."}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._json_response({"server": self.server_version, "protocol": "pmt-html-v1"})
            return
        if self.path == "/metrics":
            worker = getattr(self.server, "worker", None)
            self._json_response(worker.cache_stats() if worker is not None else {})
            return
        self._json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        """Convert one path or a batch of paths using the project configuration."""
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path in {"/", "/convert", "/convert/raw", "/preview", "/preview/raw"}:
                paths = [payload.get("path", "manuscript.md")]
                batch = False
            elif self.path == "/batch":
                paths = [item["path"] for item in payload]
                batch = True
            else:
                self._json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            mode = "preview" if self.path in {"/preview", "/preview/raw"} else "exact"
            if self.path in {"/convert/raw", "/preview/raw", "/preview"}:
                self._html_response(self.server.worker.convert(Path(paths[0]), mode=mode))
                return
            results = []
            for path in paths:
                result = self.server.worker.convert(Path(path), mode=mode)
                results.append(
                    {
                        "path": path,
                        "output": result.html,
                        "timings": result.timings,
                        "cache_hit": result.cache_hit,
                        "mode": result.mode,
                    }
                )
            self._json_response(results if batch else results[0])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError, RuntimeError) as exc:
            self._json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        """Keep request logging in the PMT server log instead of stderr."""
        return


def main() -> int:
    """Start the project-bound PMT HTML HTTP service."""
    settings = CliApp.run(ServerRuntimeSettings)
    config = json.loads(settings.config.read_text(encoding="utf-8"))
    worker_config_path = PMT_DIR / "pandoc-server-worker.json"
    worker_config_path.write_text(
        json.dumps(
            {
                # Match the Haskell worker's record field names used by Aeson.
                "projectDir": config["project_dir"],
                "pandocArgs": config["pandoc_args"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config["worker_config"] = str(worker_config_path)
    worker = PandocWorker(config, PMT_DIR / "pandoc-server-worker.log")
    server = ThreadingHTTPServer((settings.host, settings.port), PmtHtmlRequestHandler)
    server.worker = worker  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        worker.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
