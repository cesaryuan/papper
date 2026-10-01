"""Run the project-bound Papper HTML service for repeated editor/CLI builds.

One serialized Haskell worker keeps Pandoc, crossref, and citation caches alive.
The HTTP layer refreshes project metadata/dependencies, snapshots the requested
Markdown, and applies the same HTML postprocessing as a normal build. Start it
with `python -m pandoc_manuscript.commands.pandoc_server_runtime --config PATH`;
`papper build html --start-server` prepares that configuration automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
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

from ..html.postprocess import postprocess_html_text
from ..html.server_metadata import ProjectMetadataCache
from ..runtime.logging import log_debug, log_warning
from ..runtime.paths import PMT_TOOLS_BIN_DIR, process_temp_dir, project_state_dir
from .server_assets import ProjectAssetCache
from .pandoc_server import split_server_command


class ServerRuntimeSettings(BaseSettings):
    """CLI settings for the internal project-bound HTTP runtime."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    config: Path = Field(default="pandoc-server-config.json", description="Project-bound server configuration")  # Prepared by the HTML build
    host: str = Field(default="127.0.0.1", description="HTTP bind host")  # Local editor/CLI clients
    port: int = Field(default=3030, description="HTTP port")  # Reused between builds


@dataclass(frozen=True)
class ConversionResult:
    """Processed HTML and observable timing/cache details for one request."""

    html: str
    timings: dict[str, float]
    cache_hit: bool
    mode: str
    citeproc_cache_hit: bool = False


class PandocWorker:
    """Own a persistent worker, bounded HTML cache, and dependency-aware metadata."""

    def __init__(self, config: dict[str, Any], log_path: Path) -> None:
        """Allocate isolated intermediate storage and start the native worker."""
        self._config = config
        self._project = Path(config["project_dir"]).resolve()
        self._lock = threading.Lock()
        self._work_dir = process_temp_dir() / "pandoc-server-output"
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._assets = ProjectAssetCache(config)
        self._metadata = ProjectMetadataCache(config, self._work_dir)
        self._result_cache: OrderedDict[str, str] = OrderedDict()
        self._cache_bytes = 0
        self._requests = self._html_hits = self._citation_hits = 0
        self._log_path = log_path
        # Intermediate files live outside the manuscript project. Authorize
        # only this process's work directory in the private native protocol.
        worker_config = Path(config["worker_config"])
        worker_config.parent.mkdir(parents=True, exist_ok=True)
        worker_config.write_text(json.dumps({
            "projectDir": str(self._project), "pandocArgs": config["pandoc_args"],
            "workDirs": [str(self._work_dir.resolve())],
        }, ensure_ascii=False), encoding="utf-8")
        self._process = self._start_process(config, log_path)

    @staticmethod
    def _resolve_command(config: dict[str, Any]) -> list[str]:
        """Resolve an explicit worker or the project-managed native executable."""
        configured = os.environ.get("PMT_PANDOC_SERVER_WORKER_COMMAND")
        if configured:
            return split_server_command(configured)
        names = ("pmt-pandoc-worker.exe", "pmt-pandoc-worker") if os.name == "nt" else ("pmt-pandoc-worker",)
        for name in names:
            candidate = PMT_TOOLS_BIN_DIR / name
            if candidate.is_file():
                return [str(candidate)]
        raise FileNotFoundError(
            "Papper Pandoc worker not found. Build scripts/pandoc-server and install "
            "pmt-pandoc-worker into ~/.papper/tools/bin, or set PMT_PANDOC_SERVER_WORKER_COMMAND"
        )

    @classmethod
    def _start_process(cls, config: dict[str, Any], log_path: Path) -> subprocess.Popen[str]:
        """Launch the line-based worker with the project's filter environment."""
        command = [*cls._resolve_command(config), "--config", str(Path(config["worker_config"]).resolve())]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                text=True, encoding="utf-8", errors="replace", cwd=config["project_dir"],
                env=os.environ.copy(), bufsize=1,
            )
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise RuntimeError("Could not open the Papper Pandoc worker protocol streams")
        return process

    def _request(self, source: Path, output: Path, *, mode: str, fingerprint: str,
                 metadata_file: Path | None, environment: dict[str, str | None],
                 resource_paths: tuple[Path, ...]) -> dict[str, Any]:
        """Execute one private request; callers hold the conversion lock."""
        if not self.is_alive():
            log_warning("[Pandoc server] Worker exited; restarting for the next conversion")
            for stream in (self._process.stdin, self._process.stdout):
                if stream is not None:
                    stream.close()
            self._process = self._start_process(self._config, self._log_path)
        request = {
            "input": str(source), "output": str(output), "mode": mode,
            "assetFingerprint": fingerprint, "filterEnvironment": environment,
            "resourcePaths": [str(path) for path in resource_paths],
            "remoteResources": self._assets.remote.paths,
        }
        bundle, bundle_paths = self._assets.lua_bundle(self._work_dir)
        if bundle is not None:
            request["luaBundle"] = str(bundle)
            request["luaBundlePaths"] = [str(path) for path in bundle_paths]
        if metadata_file is not None:
            request["metadataFile"] = str(metadata_file)
        assert self._process.stdin is not None and self._process.stdout is not None
        self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        response = self._process.stdout.readline()
        if not response:
            raise RuntimeError("Papper Pandoc worker exited without a response")
        result = json.loads(response)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error", "Papper Pandoc worker conversion failed")))
        return result

    def convert(self, input_path: Path, *, mode: str = "exact") -> ConversionResult:
        """Convert a project source while coalescing concurrent identical requests."""
        started = time.perf_counter()
        if mode not in {"exact", "preview"}:
            raise ValueError(f"Unknown conversion mode: {mode}")
        source = (self._project / input_path).resolve()
        if not source.is_relative_to(self._project):
            raise ValueError(f"Path is outside the Papper project: {source}")
        # Serialize dependency discovery, metadata updates, and conversion as one
        # transaction; otherwise simultaneous requests can reuse mismatched assets.
        with self._lock:
            wait_ms = (time.perf_counter() - started) * 1000
            result = self._convert(source, mode=mode)
            result.timings["queue"] = round(wait_ms, 3)
            result.timings["total"] = round((time.perf_counter() - started) * 1000, 3)
            return result

    def _convert(self, source: Path, *, mode: str) -> ConversionResult:
        """Prepare one immutable source snapshot and cache only successful HTML."""
        asset_started = time.perf_counter()
        self._assets.begin()
        text = self._assets.read(source)[0].decode("utf-8-sig").replace("\r\n", "\n")
        metadata, metadata_file, body, environment = self._metadata.refresh(source, text)
        asset_digest = self._assets.refresh(
            metadata, source=source, extra_paths=self._metadata.style_paths(source),
        )
        source_digest = self._assets.source_digest(source)
        timings = {"asset_cache": round((time.perf_counter() - asset_started) * 1000, 3)}
        cache_key = hashlib.sha256(f"{mode}:{source}:{source_digest}:{asset_digest}".encode("utf-8")).hexdigest()
        self._requests += 1
        cached = self._result_cache.get(cache_key) if self._assets.cacheable else None
        if cached is not None:
            self._result_cache.move_to_end(cache_key)
            self._html_hits += 1
            return ConversionResult(cached, timings, True, mode)

        # Both sources and outputs use the private work directory. The preview
        # parses the whole document: section AST merging loses global identifiers,
        # reference definitions, and footnote context across heading boundaries.
        with tempfile.NamedTemporaryFile(prefix=f"{source.stem}-", suffix=source.suffix, dir=self._work_dir,
                                         mode="w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            snapshot = Path(handle.name)
        output = snapshot.with_suffix(".html")
        try:
            native_started = time.perf_counter()
            worker_result = self._request(
                snapshot, output, mode=mode,
                fingerprint=asset_digest if self._assets.cacheable else str(time.monotonic_ns()),
                metadata_file=metadata_file, environment=environment,
                resource_paths=self._assets.resource_roots(source),
            )
            timings["worker"] = round((time.perf_counter() - native_started) * 1000, 3)
            timings.update({f"filter_{name.removeprefix('papper-embedded-')}": round(float(value), 3)
                            for name, value in worker_result.get("filters_ms", {}).items()})
            read_started = time.perf_counter()
            raw_html = output.read_text(encoding="utf-8")
            timings["read_output"] = round((time.perf_counter() - read_started) * 1000, 3)
            post_started = time.perf_counter()
            html = postprocess_html_text(raw_html, pandoc_metadata=metadata, skip_author_info=mode == "preview")
            timings["postprocess"] = round((time.perf_counter() - post_started) * 1000, 3)
            citation_hit = bool(worker_result.get("citeproc_cache_hit", False))
            self._citation_hits += int(citation_hit)
            if self._assets.cacheable:
                self._result_cache[cache_key] = html
                self._cache_bytes += len(html.encode("utf-8"))
                # Bound both entry count and bytes; an unusually large HTML must
                # not displace the manuscript process's entire working memory.
                while len(self._result_cache) > 8 or self._cache_bytes > 32 * 1024 * 1024:
                    _, discarded = self._result_cache.popitem(last=False)
                    self._cache_bytes -= len(discarded.encode("utf-8"))
            log_debug(f"[Pandoc server] {source.name}: worker={timings['worker']:.1f} ms, citation_cache={citation_hit}")
            return ConversionResult(html, timings, False, mode, citation_hit)
        finally:
            snapshot.unlink(missing_ok=True)
            output.unlink(missing_ok=True)

    def close(self) -> None:
        """Stop only this managed worker and release its protocol handles."""
        with self._lock:
            if self.is_alive():
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait()
            for stream in (self._process.stdin, self._process.stdout):
                if stream is not None:
                    stream.close()

    def is_alive(self) -> bool:
        """Report native worker health without executing an extra conversion."""
        return self._process.poll() is None

    def cache_stats(self) -> dict[str, int]:
        """Expose whole-HTML and citation reuse as separate diagnostic counters."""
        with self._lock:
            return {"html_entries": len(self._result_cache), "html_bytes": self._cache_bytes,
                    "asset_files": self._assets.asset_count, "requests": self._requests,
                    "html_hits": self._html_hits, "citeproc_hits": self._citation_hits}


class PmtHtmlRequestHandler(BaseHTTPRequestHandler):
    """Serve project-aware conversions and health endpoints."""

    server_version = "PMT-Pandoc-Server/0.2"
    protocol_version = "HTTP/1.1"
    # Persistent local clients otherwise pay TCP delayed-ACK latency when the
    # response headers and body are written as separate small packets.
    disable_nagle_algorithm = True

    def _json_response(self, payload: object, status: int = HTTPStatus.OK) -> None:
        """Write a compact UTF-8 JSON response with an explicit byte length."""
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, result: ConversionResult) -> None:
        """Return HTML directly with independent HTML/citation cache diagnostics."""
        body = result.html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-PMT-Cache", "hit" if result.cache_hit else "miss")
        self.send_header("X-PMT-Citeproc-Cache", "skipped" if result.cache_hit else "hit" if result.citeproc_cache_hit else "miss")
        self.send_header("X-PMT-Mode", result.mode)
        self.send_header("Server-Timing", ", ".join(f"{name};dur={value:.3f}" for name, value in result.timings.items()))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        """Handle version/health and cache diagnostics."""
        worker = getattr(self.server, "worker", None)
        if self.path == "/version":
            if worker is None:
                self._json_response({"error": "Pandoc worker is not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
            else:
                # A dead child can be restarted on conversion; expose it without
                # making the supervisor kill a recoverable HTTP service.
                self._json_response({"server": self.server_version, "protocol": "pmt-html-v1", "worker_alive": worker.is_alive()})
        elif self.path == "/metrics":
            self._json_response(worker.cache_stats() if worker is not None else {})
        else:
            self._json_response({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        """Convert one source or a batch, consuming the body before responding."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 1024 * 1024:
                self.close_connection = True
                self._json_response({"error": "Invalid request body length"}, HTTPStatus.BAD_REQUEST)
                return
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
                results.append({"path": path, "output": result.html, "timings": result.timings,
                                "cache_hit": result.cache_hit, "citeproc_cache_hit": result.citeproc_cache_hit,
                                "mode": result.mode})
            self._json_response(results if batch else results[0])
        except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
            self._json_response({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: object) -> None:
        """Keep routine request chatter out of server logs."""
        return


def main() -> int:
    """Load a project configuration and supervise its HTTP/native workers."""
    settings = CliApp.run(ServerRuntimeSettings)
    config = json.loads(settings.config.read_text(encoding="utf-8"))
    os.chdir(config["project_dir"])
    state = project_state_dir(Path(config["project_dir"]))
    config["worker_config"] = str(state / "pandoc-server-worker.json")
    worker = PandocWorker(config, state / "pandoc-server-worker.log")
    try:
        server = ThreadingHTTPServer((settings.host, settings.port), PmtHtmlRequestHandler)
        server.daemon_threads = True
        server.worker = worker  # type: ignore[attr-defined]
        try:
            server.serve_forever()
        finally:
            server.server_close()
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
