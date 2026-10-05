# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Install the Typst CLI matching Cargo.lock for native release builds.

Run `uv run --script tools/ci/prepare-typst.py` from any directory. The script
reads the repository's locked Typst library version, downloads the official
archive for this host, verifies its release SHA-256 digest, and copies only the
CLI into .pmt/typst/bin. GitHub Actions receives that directory through
GITHUB_PATH; local shells and manylinux containers must add it to PATH before
running papper-dev wheel. No system installation or user Typst is replaced.
Set GITHUB_TOKEN to authenticate release metadata requests and avoid the shared
runner IP's anonymous API rate limit. Archive downloads do not receive the token.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


def locked_version(root: Path) -> str:
    """Read the compiler pin from Cargo.lock instead of maintaining a second version."""
    with (root / "Cargo.lock").open("rb") as source:
        packages = tomllib.load(source)["package"]
    versions = {p["version"] for p in packages if p["name"] == "typst-library"}
    if len(versions) != 1:
        raise RuntimeError(f"Expected one locked Typst library version, found {versions}")
    return versions.pop()


def download(url: str) -> bytes:
    """Fetch release data with optional GitHub API authentication and a bounded timeout."""
    headers = {"User-Agent": "papper-release-build"}
    endpoint = urllib.parse.urlsplit(url)
    if endpoint.scheme == "https" and endpoint.netloc == "api.github.com":
        # Shared CI runner IPs can exhaust the anonymous API quota before this build starts.
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        mode = "with" if token else "without"
        print(f"[papper CI] Fetching GitHub release metadata {mode} token authentication")
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def install(root: Path, version: str) -> Path:
    """Install only the matching executable, verifying archives before reading their contents."""
    architecture = {"AMD64": "x86_64", "arm64": "aarch64"}.get(
        platform.machine(), platform.machine()
    )
    targets = {
        "Windows": "pc-windows-msvc",
        "Darwin": "apple-darwin",
        "Linux": "unknown-linux-musl",
    }
    system = platform.system()
    if system not in targets or architecture not in {"x86_64", "aarch64"}:
        raise RuntimeError(f"Unsupported Typst build host: {system}/{architecture}")
    target = f"{architecture}-{targets[system]}"
    binary_name = "typst.exe" if system == "Windows" else "typst"
    directory = root / ".pmt" / "typst" / "bin"
    executable = directory / binary_name
    if executable.is_file():
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.split()[:2] == ["typst", version]:
            print(f"[papper CI] Reusing Typst {version}: {executable}")
            return directory

    extension = "zip" if system == "Windows" else "tar.xz"
    name = f"typst-{target}.{extension}"
    release = json.loads(download(f"https://api.github.com/repos/typst/typst/releases/tags/v{version}"))
    asset = next((asset for asset in release["assets"] if asset["name"] == name), None)
    if asset is None:
        raise RuntimeError(f"Official Typst {version} release has no {name}")
    url = f"https://github.com/typst/typst/releases/download/v{version}/{name}"
    print(f"[papper CI] Downloading Typst {version}: {name}")
    archive = download(url)
    digest = f"sha256:{hashlib.sha256(archive).hexdigest()}"
    if asset.get("digest") != digest:
        raise RuntimeError(f"Typst archive digest mismatch for {name}")

    # Extract one known member rather than trusting archive paths or symlinks.
    member = f"typst-{target}/{binary_name}"
    if extension == "zip":
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            binary = package.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as package:
            entry = package.getmember(member)
            if not entry.isfile():
                raise RuntimeError(f"Typst release member is not a regular file: {member}")
            stream = package.extractfile(entry)
            if stream is None:
                raise RuntimeError(f"Typst release omitted executable: {member}")
            with stream:
                binary = stream.read()

    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="typst-install-", dir=directory.parent) as work:
        staged = Path(work) / binary_name
        staged.write_bytes(binary)
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result = subprocess.run([str(staged), "--version"], capture_output=True, text=True, check=True)
        if result.stdout.split()[:2] != ["typst", version]:
            raise RuntimeError(f"Typst executable version mismatch: {result.stdout.strip()}")
        os.replace(staged, executable)
    print(f"[papper CI] Installed {result.stdout.strip()}: {executable}")
    return directory


def main() -> None:
    """Install the locked compiler and expose its directory to later workflow steps."""
    root = Path(__file__).resolve().parents[2]
    directory = install(root, locked_version(root))
    if github_path := os.environ.get("GITHUB_PATH"):
        with Path(github_path).open("a", encoding="utf-8") as output:
            output.write(f"{directory}\n")
    print(f"[papper CI] MiTeX build PATH directory: {directory}")


if __name__ == "__main__":
    main()
