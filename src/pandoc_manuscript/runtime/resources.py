"""Locate papper package resources from a source tree or an installed wheel."""

from __future__ import annotations

from importlib import resources
import os
from pathlib import Path
from typing import Iterable


PACKAGE_NAME = "pandoc_manuscript"


def package_root() -> Path:
    """Return the installed package directory as a filesystem path."""
    return Path(str(resources.files(PACKAGE_NAME)))


def package_resource_path(relative_path: str | Path) -> Path:
    """Return a filesystem path for a resource bundled inside the papper package."""
    return package_root() / Path(relative_path)


def source_tree_root() -> Path | None:
    """Return the papper repository root when running from this source checkout."""
    # runtime/resources.py lives one level deeper than the package root after
    # the support-module layout refactor, so the source checkout root is +1 up.
    root = Path(__file__).resolve().parents[3]
    # Detect the tool repository itself, not a generated manuscript project.
    if (root / "pyproject.toml").is_file() and (root / "src" / PACKAGE_NAME).is_dir():
        return root
    return None


def native_pandoc_executable() -> Path | None:
    """Locate the wheel's shared CLI/worker, then a source build or installed worker."""
    from .paths import PMT_TOOLS_BIN_DIR

    name = "pmt-pandoc-worker.exe" if os.name == "nt" else "pmt-pandoc-worker"
    bundled = package_resource_path("bin") / name
    if bundled.is_file():
        return bundled
    root = source_tree_root()
    if root is not None:
        builds = (root / "scripts/pandoc-server/dist-newstyle/build").glob(
            f"*/ghc-*/pmt-pandoc-server-*/x/pmt-pandoc-worker/build/pmt-pandoc-worker/{name}"
        )
        candidates = [path for path in builds if path.is_file()]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)
    installed = PMT_TOOLS_BIN_DIR / name
    return installed if installed.is_file() else None


def template_root() -> Path:
    """Return a filesystem root containing runtime Pandoc resources."""
    source_root = source_tree_root()
    if source_root is not None:
        return source_root

    packaged_root = resources.files(PACKAGE_NAME)
    if (packaged_root / "pandoc").is_dir():
        return Path(str(packaged_root))
    raise RuntimeError("Could not locate packaged Pandoc manuscript runtime resources.")


def project_template_root() -> Path:
    """Return the root copied by `papper init` into generated manuscript projects."""
    source_root = source_tree_root()
    if source_root is not None:
        return source_root / "template"

    packaged_root = resources.files(PACKAGE_NAME).joinpath("_template")
    if (packaged_root / "manuscript.md").is_file():
        return Path(str(packaged_root))
    raise RuntimeError("Could not locate packaged manuscript project template resources.")


def iter_project_template_entries(lang: str | None = None) -> Iterable[tuple[str, str]]:
    """Yield source and destination pairs for the selected `papper init` language."""
    normalized_lang = (lang or "").strip().replace("_", "-").casefold()
    manuscript_source = "manuscript-cn.md" if normalized_lang == "zh-cn" else "manuscript.md"
    reply_source = (
        "reply_to_reviewers-cn.md" if normalized_lang == "zh-cn" else "reply_to_reviewers.md"
    )
    yield from (
        (".agents", ".agents"),
        (".vscode", ".vscode"),
        ("examples", "examples"),
        (".gitignore", ".gitignore"),
        # ("AGENTS.md", "AGENTS.md"),
        ("CLAUDE.md", "CLAUDE.md"),
        (manuscript_source, "manuscript.md"),
        (reply_source, "reply_to_reviewers.md"),
        # A project starts with overrides only, so switching manuscript language
        # does not let copied English defaults shadow bundled Chinese defaults.
        ("style-project.yml", "style.yml"),
    )
