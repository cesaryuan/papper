"""Cache remote citation assets with conditional HTTP validation."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from ..runtime.logging import log_debug, log_warning


class RemoteCitationAssets:
    """Keep project-local downloads and validate active assets every five minutes."""

    def __init__(self, directory: Path) -> None:
        """Own persistent downloads and the current conversion dependency graph."""
        self.directory = directory
        self.validation_seconds = 300.0
        self.paths: dict[str, str] = {}
        self._entries: dict[str, dict] = {}
        self._active: dict[str, str] = {}

    def start_graph(self) -> None:
        """Discard unused URL mappings when local metadata/default dependencies change."""
        self.paths.clear()
        self._active.clear()

    def validate_active(self) -> None:
        """Refresh expired remote files before checking local dependency fingerprints."""
        for url, kind in tuple(self._active.items()):
            self.ensure(url, kind)

    @staticmethod
    def _publish(path: Path, content: bytes) -> None:
        """Replace downloads atomically without exposing partial files to Pandoc."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(content)
            target = Path(temporary.name)
        try:
            target.replace(path)
        finally:
            target.unlink(missing_ok=True)

    def _entry(self, url: str, kind: str) -> dict:
        """Load persistent validators and isolate URLs with identical basenames."""
        if url in self._entries:
            return self._entries[url]
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        directory = self.directory / key
        metadata = directory / "metadata.json"
        try:
            entry = json.loads(metadata.read_text(encoding="utf-8"))
            if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str) or not entry["filename"]:
                raise ValueError("Invalid remote citation cache metadata")
            if entry.get("url") != url or Path(entry["filename"]).name != entry["filename"]:
                raise ValueError("Invalid remote citation cache metadata")
            float(entry.get("checked_at", 0))
        except (OSError, ValueError, TypeError):
            name = Path(urlsplit(url).path).name or "citation-asset"
            if kind in {"csl", "citation-style"}:
                name = name if name.endswith((".csl", ".xml")) else name + ".csl"
            elif kind == "citation-abbreviations" and not name.endswith(".json"):
                name += ".json"
            entry = {"url": url, "filename": name, "checked_at": 0.0}
        self._entries[url] = entry
        return entry

    def ensure(self, url: str, kind: str) -> Path:
        """Return a validated local snapshot, using ETag/Last-Modified when available."""
        self._active[url] = kind
        entry = self._entry(url, kind)
        directory = self.directory / hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = directory / entry["filename"]
        now = time.time()
        age = now - float(entry.get("checked_at", 0))
        if path.is_file() and 0 <= age < self.validation_seconds:
            self.paths[url] = path.as_posix()
            return path
        headers = {"User-Agent": "pandoc/3.11"}
        if path.is_file():
            if entry.get("etag"):
                headers["If-None-Match"] = entry["etag"]
            if entry.get("last_modified"):
                headers["If-Modified-Since"] = entry["last_modified"]
        request = urllib.request.Request(url, headers=headers)
        # Local regression fixtures and user-local assets must bypass proxies.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"} else urllib.request.build_opener()
        try:
            with opener.open(request, timeout=15) as response:
                content = response.read()
                if kind in {"csl", "citation-style"}:
                    # Never replace a valid CSL with a proxy/CDN HTML error page.
                    if ElementTree.fromstring(content).tag != "{http://purl.org/net/xbiblio/csl}style":
                        raise ValueError(f"Remote CSL is not a CSL style: {url}")
                if kind == "citation-abbreviations":
                    json.loads(content)
                if kind == "bibliography" and not path.suffix:
                    content_type = response.headers.get_content_type()
                    extension = {"application/json": ".json", "application/vnd.citationstyles.csl+json": ".json",
                                 "application/x-yaml": ".yaml", "text/yaml": ".yaml",
                                 "application/x-research-info-systems": ".ris"}.get(content_type, ".bib")
                    entry["filename"] += extension
                    path = directory / entry["filename"]
                self._publish(path, content)
                entry.update(etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
                             resolved_url=response.geturl(), checked_at=now)
                log_debug(f"[Pandoc server] Cached remote {kind}: {url}")
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and path.is_file():
                entry["checked_at"] = now
            elif path.is_file():
                entry["checked_at"] = now - max(0, self.validation_seconds - 30)
                log_warning(f"[Pandoc server] Remote validation failed ({exc.code}); using cached {kind}: {url}")
            else:
                raise
        except (OSError, urllib.error.URLError, ValueError, ElementTree.ParseError) as exc:
            if not path.is_file():
                raise RuntimeError(f"Could not cache remote {kind}: {url}: {exc}") from exc
            entry["checked_at"] = now - max(0, self.validation_seconds - 30)
            log_warning(f"[Pandoc server] Remote validation failed; using cached {kind}: {url}: {exc}")
        self._publish(directory / "metadata.json", json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        self.paths[url] = path.as_posix()
        return path
