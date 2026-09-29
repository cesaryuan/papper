"""Resolve and install external Pandoc command-line tools."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...runtime.logging import log_info, should_log
from ...runtime.paths import (
    PMT_TOOLS_BIN_DIR,
    PMT_TOOLS_DIR,
    PMT_TOOLS_DOWNLOAD_DIR,
)


GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
USER_AGENT = "papper"
TOOL_REPOS = {
    "pandoc": "jgm/pandoc",
    "pandoc-crossref": "lierdakil/pandoc-crossref",
}
TOOL_CACHE: dict[str, "ResolvedTool"] = {}
PROXY_LOGGED = False
DOWNLOAD_CHUNK_SIZE = 256 * 1024
PROGRESS_BAR_WIDTH = 28
PROGRESS_LINE_LENGTH = 0
MIN_PANDOC_VERSION = (3, 11)


@dataclass(frozen=True)
class ResolvedTool:
    """Resolved executable path and where it came from."""

    name: str
    executable: Path
    source: str


@dataclass(frozen=True)
class ProxyConfig:
    """Proxy selected for downloading tool releases."""

    proxy: str | None
    source: str


def executable_name(command: str) -> str:
    """Return the platform-specific executable file name."""
    return f"{command}.exe" if os.name == "nt" else command


def current_platform_key() -> tuple[str, str]:
    """Return normalized OS and CPU architecture names for release assets."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = machine
    return system, arch


def asset_preferences(tool: str) -> list[str]:
    """Return release asset name fragments in preferred order for this platform."""
    system, arch = current_platform_key()
    if tool == "pandoc":
        if system == "windows":
            return ["windows-x86_64.zip"]
        if system == "darwin":
            return ["arm64-macOS.zip"] if arch == "arm64" else ["x86_64-macOS.zip"]
        if system == "linux":
            return ["linux-arm64.tar.gz"] if arch == "arm64" else ["linux-amd64.tar.gz"]
    if tool == "pandoc-crossref":
        if system == "windows":
            return ["Windows-X64.7z"]
        if system == "darwin":
            return ["macOS-ARM64.tar.xz"] if arch == "arm64" else ["macOS-X64.tar.xz"]
        if system == "linux":
            return ["Linux-ARM64.tar.xz"] if arch == "arm64" else ["Linux-X64.tar.xz"]
    raise RuntimeError(f"Unsupported platform for automatic {tool} install: {system}/{arch}")


def https_proxy_from_env() -> str | None:
    """Return HTTPS_PROXY from the environment, preserving explicit user intent."""
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")


def system_https_proxy() -> str | None:
    """Return the OS HTTPS proxy when Python can read one."""
    proxies: dict[str, str] = {}
    if sys.platform == "win32" and hasattr(urllib.request, "getproxies_registry"):
        proxies = urllib.request.getproxies_registry()
    elif sys.platform == "darwin" and hasattr(urllib.request, "getproxies_macosx_sysconf"):
        proxies = urllib.request.getproxies_macosx_sysconf()
    return proxies.get("https") or proxies.get("http")


def download_proxy_config() -> ProxyConfig:
    """Choose the proxy used for release metadata and asset downloads."""
    proxy = https_proxy_from_env()
    if proxy:
        return ProxyConfig(proxy, "HTTPS_PROXY")
    proxy = system_https_proxy()
    if proxy:
        return ProxyConfig(proxy, "system proxy")
    return ProxyConfig(None, "direct")


def open_download_url(request: urllib.request.Request, timeout: int):
    """Open a release URL with HTTPS_PROXY, system proxy, or direct access."""
    global PROXY_LOGGED

    proxy = download_proxy_config()
    if proxy.proxy:
        handler = urllib.request.ProxyHandler({"https": proxy.proxy})
        if not PROXY_LOGGED:
            log_info(f"[TOOLS] Using {proxy.source} for downloads.")
            PROXY_LOGGED = True
    else:
        handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)
    return opener.open(request, timeout=timeout)


def format_size(size: int) -> str:
    """Return a compact binary size string for download progress."""
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GiB"


def response_content_length(response: Any) -> int | None:
    """Return a positive Content-Length value when the server provides one."""
    value = response.headers.get("Content-Length")
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def progress_line(downloaded: int, total: int | None) -> str:
    """Format a single-line download progress indicator."""
    if not total:
        return f"[TOOLS] Downloaded {format_size(downloaded)}"
    ratio = min(max(downloaded / total, 0), 1)
    filled = int(PROGRESS_BAR_WIDTH * ratio)
    bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
    percent = ratio * 100
    return f"[TOOLS] Downloading [{bar}] {percent:5.1f}% {format_size(downloaded)}/{format_size(total)}"


def write_progress(downloaded: int, total: int | None, *, final: bool = False) -> None:
    """Write download progress when INFO logs are enabled."""
    global PROGRESS_LINE_LENGTH

    if not should_log("INFO"):
        return
    line = progress_line(downloaded, total)
    padding = " " * max(PROGRESS_LINE_LENGTH - len(line), 0)
    sys.stdout.write("\r" + line + padding)
    if final:
        sys.stdout.write("\n")
        PROGRESS_LINE_LENGTH = 0
    else:
        PROGRESS_LINE_LENGTH = len(line)
    sys.stdout.flush()


def copy_response_with_progress(response: Any, handle: Any) -> None:
    """Copy a response body to disk while updating terminal progress."""
    total = response_content_length(response)
    downloaded = 0
    while True:
        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
        if not chunk:
            break
        handle.write(chunk)
        downloaded += len(chunk)
        write_progress(downloaded, total)
    write_progress(downloaded, total, final=True)
    if total is not None and downloaded != total:
        raise RuntimeError(f"Incomplete download: expected {total} bytes, received {downloaded}")
    if not downloaded:
        raise RuntimeError("Download returned an empty response")


def request_json(url: str) -> dict[str, Any]:
    """Fetch a JSON document with a GitHub-friendly user agent."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with open_download_url(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch release metadata from {url}: {exc}") from exc


def latest_release(tool: str) -> dict[str, Any]:
    """Fetch the latest GitHub release metadata for a managed tool."""
    return request_json(GITHUB_API.format(repo=TOOL_REPOS[tool]))


def release_by_tag(tool: str, tag: str) -> dict[str, Any]:
    """Fetch GitHub release metadata by tag name."""
    url = f"https://api.github.com/repos/{TOOL_REPOS[tool]}/releases/tags/{tag}"
    return request_json(url)


def select_release_asset(tool: str, release: dict[str, Any]) -> dict[str, str]:
    """Choose the best release asset for the current platform."""
    assets = release.get("assets") or []
    for preference in asset_preferences(tool):
        for asset in assets:
            name = str(asset.get("name", ""))
            url = str(asset.get("browser_download_url", ""))
            if preference.lower() in name.lower() and url:
                return {"name": name, "url": url}
    names = ", ".join(str(asset.get("name", "")) for asset in assets)
    raise RuntimeError(f"No compatible {tool} release asset found. Available assets: {names}")


def download_asset(url: str, target: Path, *, force: bool = False) -> None:
    """Publish only complete downloads so interruptions cannot poison the cache."""
    if not force and target.exists() and target.stat().st_size > 0:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    log_info(f"[TOOLS] Downloading {url} -> {target}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            with open_download_url(request, timeout=300) as response:
                copy_response_with_progress(response, handle)
        temporary.replace(target)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not download {url}: {exc}") from exc
    finally:
        # KeyboardInterrupt also reaches this cleanup; abandoned .part files are never reused.
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract_archive(archive: Path, target: Path) -> None:
    """Extract a downloaded tool archive into a clean working directory."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    suffixes = "".join(archive.suffixes).lower()
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive) as zip_file:
            zip_file.extractall(target)
        return
    if suffixes.endswith((".tar.gz", ".tar.xz")):
        with tarfile.open(archive) as tar_file:
            tar_file.extractall(target)
        return
    if suffixes.endswith(".7z"):
        try:
            import py7zr
        except ImportError as exc:
            raise RuntimeError("py7zr is required to unpack pandoc-crossref Windows releases.") from exc
        with py7zr.SevenZipFile(archive) as archive_file:
            archive_file.extractall(path=target)
        return
    raise RuntimeError(f"Unsupported tool archive format: {archive.name}")


def find_executable(root: Path, command: str) -> Path:
    """Find a command executable inside an extracted release archive."""
    expected_name = executable_name(command).lower()
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == expected_name]
    if not matches:
        raise RuntimeError(f"Could not find {expected_name} in extracted archive: {root}")
    return sorted(matches, key=lambda path: len(path.parts))[0]


def install_release_tool(
    tool: str,
    release: dict[str, Any] | None = None,
    *,
    force_download: bool = False,
) -> ResolvedTool:
    """Download the latest compatible release asset and install its executable."""
    release = release or latest_release(tool)
    tag = str(release.get("tag_name") or release.get("name") or "latest").lstrip("v")
    asset = select_release_asset(tool, release)
    archive = PMT_TOOLS_DOWNLOAD_DIR / asset["name"]
    download_asset(asset["url"], archive, force=force_download)

    with tempfile.TemporaryDirectory(prefix=f"papper-{tool}-{tag}-") as working:
        extract_dir = Path(working)
        for attempt in range(2):
            try:
                extract_archive(archive, extract_dir)
                executable = find_executable(extract_dir, tool)
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                if not usable_tool(executable, tool):
                    raise RuntimeError(f"Archive contains an unusable {tool} executable")
                break
            except Exception as exc:
                # Repair legacy partial archives, including ZIP, tar and 7z failures.
                archive.unlink(missing_ok=True)
                if attempt:
                    raise RuntimeError(f"Could not unpack a usable {tool} release from {asset['url']}: {exc}") from exc
                log_info(f"[TOOLS] Invalid {tool} archive ({exc}); downloading again")
                download_asset(asset["url"], archive, force=True)

        PMT_TOOLS_BIN_DIR.mkdir(parents=True, exist_ok=True)
        installed = PMT_TOOLS_BIN_DIR / executable_name(tool)
        with tempfile.NamedTemporaryFile(dir=installed.parent, prefix=tool + ".", suffix=".part", delete=False) as handle:
            staging = Path(handle.name)
        try:
            shutil.copy2(executable, staging)
            staging.replace(installed)
        finally:
            staging.unlink(missing_ok=True)

    metadata = {
        "tool": tool,
        "version": tag,
        "asset": asset["name"],
        "url": asset["url"],
        "executable": installed.as_posix(),
    }
    (PMT_TOOLS_BIN_DIR / f"{tool}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    log_info(f"[TOOLS] Installed {tool} {tag}: {installed}")
    return ResolvedTool(tool, installed, f"~/.papper/tools ({tag})")


def managed_executable(tool: str) -> Path:
    """Return the pmt-managed executable path for a tool."""
    return PMT_TOOLS_BIN_DIR / executable_name(tool)


def install_managed_tool(
    tool: str,
    release: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> ResolvedTool:
    """Install or reuse a pmt-managed tool, ignoring system PATH."""
    managed = managed_executable(tool)
    if not force and managed.exists() and usable_tool(managed, tool):
        log_info(f"[TOOLS] {tool} already installed in ~/.papper/tools: {managed}")
        return ResolvedTool(tool, managed, "~/.papper/tools")
    return install_release_tool(tool, release=release, force_download=force)


def crossref_pandoc_version(crossref: Path) -> str | None:
    """Return the Pandoc version pandoc-crossref was compiled against, if reported."""
    try:
        result = subprocess_run_version(crossref)
    except (OSError, subprocess.SubprocessError):
        return None
    marker = "built with Pandoc v"
    if marker not in result:
        return None
    return result.split(marker, 1)[1].split(",", 1)[0].strip()


def subprocess_run_version(executable: Path) -> str:
    """Run `--version` for a tool and return combined output."""
    result = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=15,
    )
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def usable_tool(executable: Path, tool: str) -> bool:
    """Reject interrupted installs and Pandoc versions below the supported 3.11 floor."""
    try:
        output = subprocess_run_version(executable)
        match = re.search(rf"(?im)^{re.escape(tool)}(?:\.exe)?\s+v?(\d+(?:\.\d+)*)", output)
        if match is None:
            raise ValueError("unrecognized version output")
        version = tuple(int(part) for part in match.group(1).split("."))
        if tool == "pandoc" and version < MIN_PANDOC_VERSION:
            raise ValueError(
                f"Pandoc {match.group(1)} is older than {'.'.join(str(part) for part in MIN_PANDOC_VERSION)}"
            )
        return True
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log_info(f"[TOOLS] Ignoring unusable {tool} at {executable}: {exc}")
        return False


def pandoc_release_for_crossref(version: str | None) -> dict[str, Any] | None:
    """Return a Pandoc release matching pandoc-crossref's compiled version."""
    if not version:
        return None
    match = re.fullmatch(r"\d+(?:\.\d+)*", version)
    if match is None or tuple(int(part) for part in version.split(".")) < MIN_PANDOC_VERSION:
        return None
    try:
        return release_by_tag("pandoc", version)
    except RuntimeError:
        log_info(f"[TOOLS] No Pandoc release found for pandoc-crossref version {version}; using latest Pandoc.")
        return None


def resolve_pandoc(required_version: str | None = None) -> ResolvedTool:
    """Resolve Pandoc, using pandoc-crossref's version when installing locally."""
    if "pandoc" in TOOL_CACHE:
        return TOOL_CACHE["pandoc"]

    system_executable = shutil.which("pandoc")
    if system_executable and usable_tool(Path(system_executable), "pandoc"):
        resolved = ResolvedTool("pandoc", Path(system_executable), "PATH")
        TOOL_CACHE["pandoc"] = resolved
        return resolved

    managed = managed_executable("pandoc")
    if managed.exists() and usable_tool(managed, "pandoc"):
        resolved = ResolvedTool("pandoc", managed, "~/.papper/tools")
        TOOL_CACHE["pandoc"] = resolved
        return resolved

    release = pandoc_release_for_crossref(required_version)
    log_info(
        f"[TOOLS] No usable Pandoc >= {'.'.join(str(part) for part in MIN_PANDOC_VERSION)} found; "
        f"running managed setup in {PMT_TOOLS_DIR}"
    )
    if release is None:
        resolved = install_release_tool("pandoc")
    else:
        resolved = install_release_tool("pandoc", release=release)
    TOOL_CACHE["pandoc"] = resolved
    return resolved


def resolve_tool(tool: str) -> ResolvedTool:
    """Resolve a required external command, installing it locally if missing."""
    if tool == "pandoc":
        return resolve_pandoc()
    if tool in TOOL_CACHE:
        return TOOL_CACHE[tool]

    system_executable = shutil.which(tool)
    if system_executable and usable_tool(Path(system_executable), tool):
        resolved = ResolvedTool(tool, Path(system_executable), "PATH")
        TOOL_CACHE[tool] = resolved
        return resolved

    managed = managed_executable(tool)
    if managed.exists() and usable_tool(managed, tool):
        resolved = ResolvedTool(tool, managed, "~/.papper/tools")
        TOOL_CACHE[tool] = resolved
        return resolved

    log_info(f"[TOOLS] {tool} not found on PATH; installing into {PMT_TOOLS_BIN_DIR}")
    resolved = install_release_tool(tool)
    TOOL_CACHE[tool] = resolved
    return resolved


def ensure_pandoc_tools() -> tuple[ResolvedTool, ResolvedTool]:
    """Resolve both Pandoc tools, running the managed setup for old Pandoc installs."""
    installed_pandoc = shutil.which("pandoc") or str(managed_executable("pandoc"))
    if installed_pandoc and Path(installed_pandoc).exists() and not usable_tool(Path(installed_pandoc), "pandoc"):
        log_info("[TOOLS] Installed Pandoc is below 3.11; starting managed setup.")
        return setup_pandoc_tools()
    crossref = resolve_tool("pandoc-crossref")
    pandoc = resolve_pandoc(crossref_pandoc_version(crossref.executable))
    return pandoc, crossref


def setup_pandoc_tools(*, force: bool = False) -> tuple[ResolvedTool, ResolvedTool]:
    """Prepare user-scoped managed Pandoc tools shared by all Papper projects."""
    TOOL_CACHE.clear()
    crossref = install_managed_tool("pandoc-crossref", force=force)
    pandoc_release = pandoc_release_for_crossref(crossref_pandoc_version(crossref.executable))
    pandoc = install_managed_tool("pandoc", release=pandoc_release, force=force)
    TOOL_CACHE["pandoc"] = pandoc
    TOOL_CACHE["pandoc-crossref"] = crossref
    return pandoc, crossref


def pandoc_command() -> str:
    """Return the resolved Pandoc executable path and ensure filters are available."""
    pandoc, _ = ensure_pandoc_tools()
    return str(pandoc.executable)


def pandoc_tools_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment with pmt-managed tools available to child processes."""
    env = {**os.environ, **(extra_env or {})}
    bin_path = str(PMT_TOOLS_BIN_DIR.resolve())
    existing_path = env.get("PATH", "")
    if bin_path not in existing_path.split(os.pathsep):
        env["PATH"] = bin_path + (os.pathsep + existing_path if existing_path else "")
    if os.name == "nt":
        # Pandoc resolves .py JSON filters through python on PATH on Windows.
        python_dir = str(Path(sys.executable).parent)
        env["PATH"] = python_dir + os.pathsep + env["PATH"]
    return env
