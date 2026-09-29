"""Custom Hatchling build steps for native helper artifacts."""

from __future__ import annotations

import os
import atexit
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging.tags import sys_tags


class CustomBuildHook(BuildHookInterface):
    """Build and include binary-only MathType runtime tools in wheels."""

    PLUGIN_NAME = "papper-native-helpers"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Compile platform runtime tools before wheel file selection.

        Rust libraries are never copied into ``src/``, and the .NET helper is
        rebuilt into ``.pmt`` so wheel contents cannot become stale.
        """
        if self.target_name != "wheel" or version == "editable":
            return

        root = Path(self.root)
        force_include = build_data.setdefault("force_include", {})
        # A forced template directory bypasses Hatchling's normal excludes.
        # Stage only authored files so old project caches never enter the wheel.
        template_stage = Path(tempfile.mkdtemp(prefix="papper-wheel-template-"))
        atexit.register(shutil.rmtree, template_stage, ignore_errors=True)
        shutil.copytree(
            root / "template",
            template_stage / "template",
            ignore=shutil.ignore_patterns(".pmt", ".papper", ".pandoc-cache", "output", "tmp", "__pycache__", "*.pyc"),
        )
        force_include.pop("template", None)
        force_include.pop(str(root / "template"), None)
        force_include[str(template_stage / "template")] = "pandoc_manuscript/_template"
        if os.name == "nt":
            helper = self.build_mathtype_ole_helper(root)
            force_include[str(helper)] = "pandoc_manuscript/mathtype/ole_helper/bin/Release/net48/MathTypeOleHelper.exe"

        library = self.build_native_library(root, "mathtype-rust")
        force_include[str(library)] = f"pandoc_manuscript/mathtype/bin/{library.name}"

        # XITS Math is compiled into latex2wmf; ship its OFL and upstream
        # notices so installed wheels retain the required attribution.
        font_assets = root / "scripts" / "latex2wmf" / "assets" / "fonts"
        font_notices = (
            "XITS-NOTICE.txt",
            "XITS-OFL.txt",
            "XITS-README.txt",
        )
        for notice in font_notices:
            source = font_assets / notice
            if not source.exists():
                raise FileNotFoundError(f"XITS Math notice is missing: {source}")
            force_include[str(source)] = f"pandoc_manuscript/mathtype/bin/{notice}"

        # MiTeX's Typst sources are compiled into latex2wmf rather than shipped
        # as source files; retain the upstream Apache-2.0 notice beside it.
        mitex_license = root / "scripts" / "latex2wmf" / "assets" / "mitex" / "MITEX-APACHE-2.0.txt"
        if not mitex_license.exists():
            raise FileNotFoundError(f"MiTeX license is missing: {mitex_license}")
        force_include[str(mitex_license)] = "pandoc_manuscript/mathtype/bin/MITEX-APACHE-2.0.txt"

        # Native helpers make this a platform wheel even though the Python
        # package itself has no extension module or CPython ABI dependency.
        platform_tag = next(iter(sys_tags())).platform
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{platform_tag}"

    def build_native_library(self, root: Path, project_name: str) -> Path:
        """Compile only the shared library exposing the versioned C ABI."""
        manifest = root / "scripts" / project_name / "Cargo.toml"
        if not manifest.is_file():
            raise FileNotFoundError(f"{project_name} manifest is missing: {manifest}")
        if shutil.which("cargo") is None:
            raise RuntimeError("Building native libraries requires `cargo` on PATH.")
        print(f"[papper build] building {project_name} release library with cargo", flush=True)
        started = time.monotonic()
        subprocess.run(
            ["cargo", "rustc", "--locked", "--crate-type", "cdylib", "--manifest-path", str(manifest), "--lib", "--features", "ffi", "--release"],
            cwd=root, check=True,
        )
        name = project_name.replace("-", "_")
        filename = f"{name}.dll" if os.name == "nt" else f"lib{name}.{'dylib' if sys.platform == 'darwin' else 'so'}"
        # CI keeps Cargo outputs outside the source tree for persistent caching.
        # Cargo resolves a relative CARGO_TARGET_DIR against its working directory.
        target_dir = Path(os.environ.get("CARGO_TARGET_DIR") or manifest.parent / "target")
        if not target_dir.is_absolute():
            target_dir = root / target_dir
        library = target_dir / "release" / filename
        if not library.exists():
            raise FileNotFoundError(f"cargo did not create expected library: {library}")
        print(f"[papper build] {project_name} completed in {time.monotonic() - started:.1f}s", flush=True)
        return library

    def build_mathtype_ole_helper(self, root: Path) -> Path:
        """Build the Windows SDK helper once while producing the wheel."""
        project = root / "src" / "pandoc_manuscript" / "mathtype" / "ole_helper" / "MathTypeOleHelper.csproj"
        if not project.exists():
            raise FileNotFoundError(f"MathType OLE helper project is missing: {project}")
        if shutil.which("dotnet") is None:
            raise RuntimeError("Building the Windows wheel requires `dotnet` on PATH.")

        # Keep wheel builds from rewriting the tracked source-checkout fallback binary.
        output_dir = root / ".pmt" / "native-wheel" / "MathTypeOleHelper"
        print("[papper build] building MathTypeOleHelper release executable with dotnet", flush=True)
        started = time.monotonic()
        subprocess.run(
            [
                "dotnet",
                "build",
                str(project),
                "-c",
                "Release",
                "-v:quiet",
                "--output",
                str(output_dir),
            ],
            cwd=root,
            check=True,
        )
        executable = output_dir / "MathTypeOleHelper.exe"
        if not executable.exists():
            raise FileNotFoundError(f"dotnet did not create expected executable: {executable}")
        print(f"[papper build] MathTypeOleHelper completed in {time.monotonic() - started:.1f}s", flush=True)
        return executable
