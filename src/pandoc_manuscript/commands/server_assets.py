"""Track local conversion dependencies without rereading unchanged assets."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from ..runtime.paths import project_cache_dir
from .server_remote_assets import RemoteCitationAssets


class ProjectAssetCache:
    """Fingerprint defaults, metadata, partials, filters, and manuscript resources."""

    _OPTIONS = {"--defaults", "--metadata-file", "--csl", "--bibliography", "--template",
                "--include-in-header", "--include-before-body", "--include-after-body",
                "--lua-filter", "--filter", "--citation-abbreviations"}
    _YAML_PATHS = {"template", "csl", "citation-style", "bibliography", "metadata-file",
                   "metadata-files", "filters", "lua-filter", "include-in-header",
                   "include-before-body", "include-after-body", "citation-abbreviations",
                   "crossrefYaml"}
    _REFERENCES = re.compile(
        r"!?\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))|(?:src|href)=[\"']([^\"']+)[\"']"
        r"|^ {0,3}\[[^\]]+\]:\s*(?:<([^>]+)>|([^\s]+))", re.IGNORECASE | re.MULTILINE,
    )
    _PARTIALS = re.compile(r"\$([\w./-]+)\([^)]*\)\$")
    _CSL_PARENT = re.compile(r'<link\b(?=[^>]*rel=[\"\']independent-parent[\"\'])[^>]*href=[\"\']([^\"\']+)', re.IGNORECASE)
    # Batch only these audited scripts. Edited/custom scripts retain Pandoc's
    # independent Lua environments; matching names alone are insufficient.
    _BUNDLE_HASHES = {
        "normalize_chinese_numbering": "0d2278ae300e7d4452cff58b3cafb3f3aaf3fc7bd271014afe3db1e92511e242",
        "merge_table_cells": "98265b652438b1f452c20e8f35eeba5bf526bed38aabd48192863dc433c08311",
        "paragraph_custom_styles": "cd84cbd11d7663285b06e7698de320306cf8d2145c37f06b8dbefe1fa5be10c0",
        "subfigure_layout_styles": "ef52f360ffcab970b384e6f52105d56d61e64cd14bb6b398f4936f3481b53ea8",
        "revision_table_styles": "c7e95fe19e55d488ab3dd7e9e977edb9f276f61a7a7d74c47b4dc7c81fd9c53b",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        """Keep byte/digest caches and the most recent dependency graph."""
        self._config = config
        self._roots = tuple(Path(item).resolve() for item in config.get("resource_paths", []))
        self._markers: dict[Path, tuple[int, int, int] | None] = {}
        self._resolved: dict[object, tuple[tuple[tuple[Path, object], ...], Path]] = {}
        self._entries: dict[Path, tuple[tuple[int, int, int] | None, bytes | None, str]] = {}
        self._paths: tuple[Path, ...] = ()
        self._discovery_markers: tuple[object, ...] = ()
        self._context = ""
        self._source_resources: dict[Path, tuple[str, tuple[str, ...]]] = {}
        self.cacheable = True
        self._bundle_signature: tuple[object, ...] = ()
        self.remote = RemoteCitationAssets(project_cache_dir(Path(config["project_dir"])) / "pandoc-server-remote")

    def lua_bundle(self, work_dir: Path) -> tuple[Path | None, tuple[Path, ...]]:
        """Batch audited Lua filters while preserving separate script namespaces."""
        matched: dict[str, Path] = {}
        for path in self._paths:
            expected = self._BUNDLE_HASHES.get(path.stem)
            if expected is None or path.suffix != ".lua":
                continue
            try:
                content = self.read(path)[0].replace(b"\r\n", b"\n")
            except OSError:
                continue
            if hashlib.sha256(content).hexdigest() == expected:
                matched[path.stem] = path
        if len(matched) != len(self._BUNDLE_HASHES):
            return None, ()
        scripts = tuple(matched[name] for name in self._BUNDLE_HASHES)
        target = work_dir / "papper-html-filter-bundle.lua"
        if scripts != self._bundle_signature or not target.is_file():
            # Separate _ENV tables preserve script-local globals and closures.
            # document:walk applies every filter in its original order, inside
            # one Lua state instead of five Pandoc/Lua marshaling boundaries.
            paths = ", ".join(json.dumps(path.as_posix()) for path in scripts)
            target.write_text(
                "-- Apply audited Papper HTML filters in order within one Lua state\n"
                f"local paths = {{{paths}}}\n"
                "local filters = {}\n"
                "for _, path in ipairs(paths) do\n"
                "  local env = setmetatable({PANDOC_SCRIPT_FILE = path}, {__index = _G})\n"
                "  assert(loadfile(path, 't', env))()\n"
                "  filters[#filters + 1] = env\n"
                "end\n"
                "-- Walk fresh prose through each script's own callback table\n"
                "local function apply_filters(document)\n"
                "  for _, filter in ipairs(filters) do document = document:walk(filter) end\n"
                "  return document\n"
                "end\n"
                "return {{Pandoc = apply_filters}}\n", encoding="utf-8",
            )
            self._bundle_signature = scripts
        return target, scripts

    def begin(self) -> None:
        """Start a request with a fresh filesystem snapshot shared by cache layers."""
        self._markers.clear()

    def _marker(self, path: Path) -> tuple[int, int, int] | None:
        """Observe replaced, edited, and deleted files, including restored mtimes."""
        if path in self._markers:
            return self._markers[path]
        try:
            stat = path.stat()
            marker = stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns
        except FileNotFoundError:
            marker = None
        self._markers[path] = marker
        return marker

    def read(self, path: Path) -> tuple[bytes, str]:
        """Read/hash each file once per observed version and never cache failures."""
        marker = self._marker(path)
        previous = self._entries.get(path)
        if marker is not None and previous is not None and previous[0] == marker and previous[1] is not None:
            return previous[1], previous[2]
        # Publication follows a successful read: recreation after a failed read
        # must not revive the deleted file's old cache entry.
        self._entries.pop(path, None)
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        # Keep YAML/templates/manuscripts, but not large figures or executables.
        self._entries[path] = (marker, content if len(content) <= 1024 * 1024 else None, digest)
        return content, digest

    def _file_digest(self, path: Path) -> str:
        """Stream large resource hashes and reuse them without retaining binaries."""
        marker = self._marker(path)
        previous = self._entries.get(path)
        if marker is not None and previous is not None and previous[0] == marker:
            return previous[2]
        self._entries.pop(path, None)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        value = digest.hexdigest()
        self._entries[path] = (marker, None, value)
        return value

    def resource_roots(self, source: Path | None = None) -> tuple[Path, ...]:
        """Preserve explicit search paths, otherwise search beside Markdown first."""
        roots = list(self._roots)
        context = self._config.get("metadata_sources") or {}
        if source is not None and not context.get("resource_path_explicit"):
            roots.insert(0, source.parent)
        return tuple(dict.fromkeys(roots or [Path(self._config["project_dir"])]))

    def _resolve(self, raw: Any, base: Path, roots: tuple[Path, ...]) -> Path | None:
        """Resolve local dependencies through the same resource roots as Pandoc."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        raw = raw.strip().replace("${.}", str(base))
        if raw.startswith(("http:", "https:", "data:")):
            # Remote citation/template content cannot be invalidated by stat.
            self.cacheable = False
            return None
        path = Path(raw)
        key = (raw, base, roots)
        previous = self._resolved.get(key)
        if previous is not None and all(self._marker(candidate) == marker for candidate, marker in previous[0]):
            return previous[1]
        candidates = [path] if path.is_absolute() else list(dict.fromkeys([base / path, *(root / path for root in roots)]))
        examined: list[tuple[Path, object]] = []
        chosen = candidates[0]
        for candidate in candidates:
            marker = self._marker(candidate)
            examined.append((candidate, marker))
            if marker is not None and candidate.is_file():
                chosen = candidate
                break
        resolved = chosen.resolve()
        # Observe every higher-priority candidate as well as the chosen path:
        # a newly created file can supersede an existing fallback resource.
        self._resolved[key] = (tuple(examined), resolved)
        return resolved

    def _discover(self, metadata: dict[str, Any], roots: tuple[Path, ...], extra_paths: tuple[Path, ...]) -> tuple[Path, ...]:
        """Expand YAML dependencies, template partials, and local CSL parents."""
        project = Path(self._config["project_dir"])
        self.remote.start_graph()
        paths: set[Path] = set(extra_paths)
        queue: list[tuple[Path, str]] = []
        data_dirs: list[Path] = []
        expanded_csl: set[Path] = set()

        def add(raw: Any, base: Path, kind: str) -> None:
            """Register one path, retaining missing dependencies for later creation."""
            if isinstance(raw, list):
                for value in raw:
                    add(value, base, kind)
                return
            if isinstance(raw, dict):
                raw = raw.get("path")
            if kind == "filters" and raw == "citeproc":
                return
            if kind in {"filters", "--filter"} and isinstance(raw, str) and shutil.which(raw):
                raw = shutil.which(raw)
            if isinstance(raw, str) and raw.startswith(("http:", "https:")) and kind.lstrip("-") in {"csl", "citation-style", "bibliography", "citation-abbreviations"}:
                path = self.remote.ensure(raw, kind.lstrip("-"))
                registered = path in paths
            else:
                path = self._resolve(raw, base, roots)
                registered = path in paths
                if isinstance(raw, str):
                    key = (raw.strip().replace("${.}", str(base)), base, roots)
                    # Track missing higher-priority candidates as dependencies,
                    # so a newly created local asset supersedes a fallback.
                    paths.update(candidate for candidate, _ in self._resolved.get(key, ((), None))[0])
            if path is None or registered:
                return
            paths.add(path)
            if kind in {"--defaults", "defaults", "--metadata-file", "metadata-file", "metadata-files", "template", "--template", "csl", "--csl", "citation-style"}:
                queue.append((path, kind))

        args = self._config.get("pandoc_args", [])
        index = 0
        while index < len(args):
            arg, _, inline = str(args[index]).partition("=")
            if arg in self._OPTIONS:
                if inline:
                    add(inline, project, arg)
                elif index + 1 < len(args):
                    index += 1
                    add(str(args[index]), project, arg)
            index += 1
        for key in self._YAML_PATHS:
            add(metadata.get(key), project, key)
        add(metadata.get("crossrefYaml", "pandoc-crossref.yaml"), project, "crossrefYaml")
        add(str(Path.home() / ".pandoc-crossref/config.yaml"), project, "crossrefYaml")
        add(str(Path.home() / ".pandoc-crossref/config-html.yaml"), project, "crossrefYaml")
        while queue:
            path, kind = queue.pop(0)
            try:
                text = self.read(path)[0].decode("utf-8")
            except (OSError, UnicodeError):
                continue
            if kind in {"template", "--template"}:
                for partial in self._PARTIALS.findall(text):
                    add(partial if Path(partial).suffix else partial + path.suffix, path.parent, "template")
            elif kind in {"csl", "--csl", "citation-style"}:
                if path in expanded_csl:
                    continue
                expanded_csl.add(path)
                for parent in self._CSL_PARENT.findall(text):
                    basename = Path(parent).name
                    basename = basename if "." in basename else basename + ".csl"
                    parent_roots = (*roots, *(directory / "csl" for directory in data_dirs),
                                    *(directory / "csl/dependent" for directory in data_dirs))
                    local = self._resolve(basename, parent_roots[0], parent_roots)
                    paths.update(candidate for candidate, _ in self._resolved.get((basename, parent_roots[0], parent_roots), ((), None))[0])
                    if local is not None:
                        paths.add(local)
                    if local is not None and local.is_file():
                        self.remote.paths[parent] = local.as_posix()
                        queue.append((local, "csl"))
                    else:
                        remote = self.remote.ensure(parent, "csl")
                        paths.add(remote)
                        queue.append((remote, "csl"))
            else:
                try:
                    mapping = yaml.safe_load(text) or {}
                except yaml.YAMLError:
                    continue
                if not isinstance(mapping, dict):
                    continue
                if mapping.get("data-dir"):
                    data_dir = self._resolve(mapping["data-dir"], path.parent, roots)
                    if data_dir is not None:
                        data_dirs.append(data_dir)
                for key in self._YAML_PATHS | {"defaults"}:
                    add(mapping.get(key), path.parent if kind in {"--defaults", "defaults"} else project, key)
                nested = mapping.get("metadata", {})
                if isinstance(nested, dict):
                    for key in self._YAML_PATHS:
                        add(nested.get(key), project, key)
        return tuple(sorted(paths, key=str))

    def refresh(self, metadata: dict[str, Any] | None = None, *, source: Path | None = None, extra_paths: tuple[Path, ...] = ()) -> str:
        """Refresh the dependency digest, rediscovering paths only after changes."""
        metadata = metadata if metadata is not None else self._config.get("pandoc_metadata", {})
        roots = self.resource_roots(source)
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
        context = str((metadata_json, roots, extra_paths))
        if context == self._context:
            self.remote.validate_active()
        markers = tuple(self._marker(path) for path in self._paths)
        if context != self._context or markers != self._discovery_markers:
            self.cacheable = True
            self._paths = self._discover(metadata, roots, extra_paths)
            self._context = context
            self._discovery_markers = tuple(self._marker(path) for path in self._paths)
        digest = hashlib.sha256(metadata_json.encode("utf-8"))
        for path in self._paths:
            digest.update(str(path).encode("utf-8"))
            try:
                digest.update(self._file_digest(path).encode("ascii"))
            except OSError:
                digest.update(b"<missing>")
        return digest.hexdigest()

    def source_digest(self, path: Path) -> str:
        """Fingerprint prose and local resources, reusing reference discovery."""
        content, content_digest = self.read(path)
        previous = self._source_resources.get(path)
        if previous is not None and previous[0] == content_digest:
            resources = previous[1]
        else:
            text = content.decode("utf-8-sig")
            resources = tuple(next(value for value in match.groups() if value) for match in self._REFERENCES.finditer(text))
            self._source_resources[path] = (content_digest, resources)
        digest = hashlib.sha256(content_digest.encode("ascii"))
        roots = self.resource_roots(path)
        for raw in resources:
            if raw.startswith(("#", "http:", "https:", "data:", "mailto:")):
                continue
            resource = self._resolve(unquote(raw.split("#", 1)[0].split("?", 1)[0]), roots[0], roots)
            if resource is None:
                continue
            digest.update(str(resource).encode("utf-8"))
            try:
                digest.update(self._file_digest(resource).encode("ascii"))
            except OSError:
                digest.update(b"<missing>")
        return digest.hexdigest()

    @property
    def asset_count(self) -> int:
        """Return the number of tracked project assets, including missing files."""
        return len(self._paths)
