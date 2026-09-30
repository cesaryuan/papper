"""Benchmark the current Papper toolchain against a manuscript.

Run from the checkout with ``uv run python scripts/benchmark_build.py``.
Pydantic settings accept CLI options and PAPPER_BENCH_* environment variables;
PAPPER_BENCH_PANDOC selects a Pandoc executable, without installing or changing
the system version. Each sample starts a fresh Python process and runs Papper's
CLI build dispatcher. A wrapper times the real Pandoc subprocess, including its
filters and citeproc, while the parent measures process startup through exit.

Cold samples use empty, isolated Papper state; warm samples share state after
untimed warmups. Neither mode flushes the operating system's filesystem cache.
Only the worker's project state is redirected; managed tool locations are kept.
Manuscript assets and style.yml remain in place, and outputs, logs, versions,
individual timings, and summary statistics are saved in a new report directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, CliSuppress, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("papper.benchmark")
METRICS = ("process_s", "build_s", "pandoc_s", "non_pandoc_build_s")


class BenchmarkSettings(BaseSettings):
    """Configure one benchmark session through CLI options or environment."""

    model_config = SettingsConfigDict(
        env_prefix="PAPPER_BENCH_", cli_kebab_case=True,
        cli_implicit_flags=True, extra="ignore",
    )

    # ==== Inputs and toolchain
    manuscript: Path = Field(default=str(ROOT / "template/manuscript.md"), description="Markdown input")  # Manuscript and adjacent assets
    project_dir: Path = Field(default=str(ROOT), description="Build working directory")  # Additional style.yml and resource root
    pandoc: str = Field(default="pandoc", description="Pandoc command or executable path")  # Also configurable with PAPPER_BENCH_PANDOC
    targets: list[Literal["docx", "html", "json", "latex"]] = Field(default=["docx", "html", "json"], min_length=1, description="Targets to benchmark")  # Normal Papper build targets
    mathtype: Literal["metadata", "on", "off"] = Field(default="metadata", description="DOCX MathType policy")  # Metadata preserves the normal build behavior

    # ==== Sampling and reporting
    cache_modes: list[Literal["cold", "warm"]] = Field(default=["cold", "warm"], min_length=1, description="Isolated Papper cache policies")  # Cold does not mean cold OS caches
    runs: int = Field(default=7, ge=2, description="Measured samples per target and cache mode")  # At least two samples for dispersion
    warmups: int = Field(default=2, ge=1, description="Untimed samples before each measured case")  # Cold warmups also receive fresh state
    output_dir: Path = Field(default=str(ROOT / "output/benchmarks"), description="Parent directory for a new timestamped report")  # Never overwrite an earlier report

    # ==== Internal worker protocol
    worker: CliSuppress[bool] = Field(default=False)  # Child-process mode, set by the parent
    state_dir: CliSuppress[Path] = Field(default=str(ROOT / "output/benchmarks/state"))  # Temporary project state, used only by workers
    worker_output: CliSuppress[Path] = Field(default=str(ROOT / "output/benchmarks/worker.docx"))  # Exact output path for one build
    worker_metrics: CliSuppress[Path] = Field(default=str(ROOT / "output/benchmarks/worker.json"))  # Timing handoff from child to parent

    def cli_cmd(self) -> None:
        """Dispatch the benchmark controller or its isolated build worker."""
        if self.worker:
            BenchmarkWorker(self).run()
        else:
            BenchmarkSession(self).run()


class BenchmarkWorker:
    """Measure a single CLI build with isolated project state."""

    def __init__(self, settings: BenchmarkSettings) -> None:
        """Keep this sample's settings and Pandoc subprocess timings."""
        self.settings = settings
        self.pandoc_times: list[float] = []
        self.pandoc_commands: list[list[str]] = []

    def run(self) -> None:
        """Run the public CLI dispatcher and export successful-build timings."""
        from pandoc_manuscript.runtime import paths

        # Override before build imports: project caches must not read or write
        # the user's ~/.papper state, even when the input stays in its directory.
        paths.PAPPER_HOME_DIR = self.settings.state_dir

        from pandoc_manuscript import cli
        from pandoc_manuscript.commands import build
        from pandoc_manuscript.commands.setup import pandoc_tools

        original_run = build.run_command

        def timed_command(cmd: list, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
            """Time actual Pandoc execution, excluding version probes and setup."""
            executable = pandoc_tools.TOOL_CACHE.get("pandoc")
            # Other build subprocesses must remain outside the Pandoc metric
            # if Papper adds more steps through this shared command helper.
            if executable is None or Path(str(cmd[0])).resolve() != executable.executable.resolve():
                return original_run(cmd, *args, **kwargs)
            started = time.perf_counter()
            try:
                return original_run(cmd, *args, **kwargs)
            finally:
                self.pandoc_times.append(time.perf_counter() - started)
                self.pandoc_commands.append([str(part) for part in cmd])

        build.run_command = timed_command
        command = ["build", self.settings.targets[0], "-m", str(self.settings.manuscript),
                   "-o", str(self.settings.worker_output)]
        if self.settings.targets[0] == "docx" and self.settings.mathtype != "metadata":
            command.append("--mathtype" if self.settings.mathtype == "on" else "--no-mathtype")

        started = time.perf_counter()
        # Use the same CLI parser/dispatcher as papper, without the unrelated
        # PyPI update worker that cli.main starts after a completed command.
        app = CliApp.run(cli.PmtCli, cli_args=command, cli_parse_args=True)
        elapsed = time.perf_counter() - started
        if app._exit_code:
            raise RuntimeError(f"Papper build failed with exit code {app._exit_code}")
        if not self.pandoc_times or not self.settings.worker_output.is_file():
            raise RuntimeError("Build did not execute Pandoc or produce the requested output")

        pandoc_time = sum(self.pandoc_times)
        tools = pandoc_tools.TOOL_CACHE
        payload = {
            "build_s": elapsed, "pandoc_s": pandoc_time,
            "non_pandoc_build_s": elapsed - pandoc_time,
            "pandoc_commands": self.pandoc_commands,
            "pandoc_executable": str(tools["pandoc"].executable.resolve()),
            "crossref_executable": str(tools["pandoc-crossref"].executable.resolve()),
            "output_bytes": self.settings.worker_output.stat().st_size,
        }
        self.settings.worker_metrics.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BenchmarkSession:
    """Own toolchain validation, isolated samples, and persistent reports."""

    def __init__(self, settings: BenchmarkSettings) -> None:
        """Resolve inputs and prepare this session's report metadata."""
        self.settings = settings
        self.settings.manuscript = settings.manuscript.resolve(strict=True)
        self.settings.project_dir = settings.project_dir.resolve(strict=True)
        executable = shutil.which(settings.pandoc)
        if executable is None:
            raise FileNotFoundError(f"Pandoc executable not found: {settings.pandoc}")
        self.pandoc = Path(executable).resolve()
        if self.pandoc.name.lower() not in {"pandoc", "pandoc.exe"}:
            raise ValueError("Selected executable must be named pandoc or pandoc.exe for PATH resolution")
        self.environment = dict(os.environ)
        # Windows environment keys are case-insensitive; normalize PATH before
        # replacing it so an inherited 'Path' cannot hide the requested version.
        for key in list(self.environment):
            if key.upper() == "PATH":
                self.environment.pop(key)
        self.environment["PATH"] = str(self.pandoc.parent) + os.pathsep + os.environ.get("PATH", "")
        self.environment["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment["PYTHONIOENCODING"] = "utf-8"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.report_dir = settings.output_dir.resolve() / timestamp
        self.report: dict[str, Any] = {"schema_version": 1, "status": "running", "samples": [], "summary": []}

    def toolchain(self) -> dict[str, Any]:
        """Validate required tools and record actual executable/version choices."""
        from pandoc_manuscript.commands.setup import pandoc_tools

        original_path = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = self.environment["PATH"]
            pandoc_tools.TOOL_CACHE.clear()
            pandoc, crossref = pandoc_tools.ensure_pandoc_tools()
            if pandoc.executable.resolve() != self.pandoc:
                raise RuntimeError(f"Papper selected {pandoc.executable} instead of {self.pandoc}")
            # Pandoc's filters see the managed bin directory before system PATH.
            # Record its actual crossref choice, which may differ from CLI lookup.
            filter_crossref = shutil.which("pandoc-crossref", path=pandoc_tools.pandoc_tools_env()["PATH"])
            if filter_crossref is None:
                raise FileNotFoundError("pandoc-crossref is missing from the filter environment")
            return {
                "pandoc_executable": str(self.pandoc),
                "pandoc_version": pandoc_tools.subprocess_run_version(self.pandoc).strip(),
                "crossref_executable": str(crossref.executable.resolve()),
                "filter_crossref_executable": str(Path(filter_crossref).resolve()),
                "crossref_version": pandoc_tools.subprocess_run_version(Path(filter_crossref)).strip(),
            }
        finally:
            os.environ["PATH"] = original_path

    def sample(self, target: str, mode: str, index: int, warmup: bool, temporary: Path) -> None:
        """Build once, retaining logs and all measured successful samples."""
        case = f"{target}-{mode}"
        label = f"{case}-{'warmup' if warmup else 'run'}-{index:02d}"
        home = temporary / (label if mode == "cold" else case) / "state"
        home.mkdir(parents=True, exist_ok=True)
        metrics_file = temporary / f"{label}.json"
        suffix = "tex" if target == "latex" else target
        output_file = self.report_dir / f"{case}.{suffix}"
        environment = {**self.environment,
            "PAPPER_BENCH_WORKER": "true", "PAPPER_BENCH_TARGETS": json.dumps([target]),
            "PAPPER_BENCH_MANUSCRIPT": str(self.settings.manuscript),
            "PAPPER_BENCH_MATHTYPE": self.settings.mathtype,
            "PAPPER_BENCH_STATE_DIR": str(home), "PAPPER_BENCH_WORKER_OUTPUT": str(output_file),
            "PAPPER_BENCH_WORKER_METRICS": str(metrics_file),
        }
        log_file = self.report_dir / f"{label}.log"
        with log_file.open("w", encoding="utf-8") as stream:
            started = time.perf_counter()
            result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                cwd=self.settings.project_dir, env=environment, stdout=stream, stderr=subprocess.STDOUT)
            process_time = time.perf_counter() - started
        if result.returncode:
            raise RuntimeError(f"{label} failed; see {log_file}\n{log_file.read_text(encoding='utf-8')[-4000:]}")
        measured = json.loads(metrics_file.read_text(encoding="utf-8"))
        if Path(measured["pandoc_executable"]) != self.pandoc:
            raise RuntimeError(f"Unexpected Pandoc executable in {label}: {measured['pandoc_executable']}")
        LOG.info("%s: process %.3f s, Pandoc %.3f s%s", label, process_time,
                 measured["pandoc_s"], " (untimed warmup)" if warmup else "")
        if not warmup:
            self.report["samples"].append({"target": target, "cache_mode": mode, "run": index,
                "process_s": process_time, "log": log_file.name, **measured})
            self.save()

    def summarize(self) -> None:
        """Compute distributions for each target/cache case without outlier removal."""
        for target in dict.fromkeys(self.settings.targets):
            for mode in dict.fromkeys(self.settings.cache_modes):
                samples = [sample for sample in self.report["samples"]
                           if sample["target"] == target and sample["cache_mode"] == mode]
                row: dict[str, Any] = {"target": target, "cache_mode": mode, "n": len(samples)}
                for metric in METRICS:
                    values = [sample[metric] for sample in samples]
                    row[metric] = {"median": statistics.median(values), "mean": statistics.mean(values),
                                   "stdev": statistics.stdev(values), "min": min(values), "max": max(values)}
                self.report["summary"].append(row)

    def save(self) -> None:
        """Persist raw observations, including partial progress if a later build fails."""
        (self.report_dir / "results.json").write_text(
            json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def write_markdown(self) -> None:
        """Write a readable timing table with the exact measurement boundaries."""
        version = self.report["toolchain"]["pandoc_version"].splitlines()[0]
        lines = ["# Papper build benchmark", "", f"Toolchain: {version}",
            f"Input: `{self.settings.manuscript}`", f"MathType: `{self.settings.mathtype}`", "",
            "Times are seconds. Process includes Python startup, benchmark worker setup, imports, CLI build, and exit; "
            "build covers the CLI parser/dispatcher; Pandoc covers its subprocess including "
            "filters/citeproc; remaining build time covers Python setup/postprocessing and tool checks.", "",
            "Cold means empty Papper state for each sample. Warm means shared Papper state after "
            "warmups. Every sample uses a fresh Python process; OS caches are not flushed. "
            "PyPI update checks and uv environment setup are excluded. Native helper compilation, "
            "if needed during warmups, is not a controlled benchmark phase.", "",
            "| Target | Cache | N | Process median | Process mean ± SD | Pandoc median | Build median | Remaining build median |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for row in self.report["summary"]:
            process = row["process_s"]
            lines.append(f"| {row['target']} | {row['cache_mode']} | {row['n']} | "
                f"{process['median']:.4f} | {process['mean']:.4f} ± {process['stdev']:.4f} | "
                f"{row['pandoc_s']['median']:.4f} | {row['build_s']['median']:.4f} | "
                f"{row['non_pandoc_build_s']['median']:.4f} |")
        lines.extend(["", "Raw samples, tool versions, input hashes, and commands: `results.json`.", ""])
        (self.report_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self) -> None:
        """Validate the toolchain, sample cases sequentially, and emit the report."""
        from pandoc_manuscript import __version__

        toolchain = self.toolchain()
        self.report_dir.mkdir(parents=True, exist_ok=False)
        style_paths = dict.fromkeys((self.settings.manuscript.parent / "style.yml",
                                    self.settings.project_dir / "style.yml"))
        self.report.update({"started_at": datetime.now(timezone.utc).isoformat(),
            "settings": self.settings.model_dump(mode="json", exclude={"worker", "state_dir", "worker_output", "worker_metrics"}),
            "toolchain": toolchain, "papper_version": __version__,
            "python": sys.version, "python_executable": sys.executable, "platform": platform.platform(),
            "processor": platform.processor(), "cpu_count": os.cpu_count(),
            "input_sha256": hashlib.sha256(self.settings.manuscript.read_bytes()).hexdigest(),
            "style_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in style_paths if path.is_file()},
        })
        self.save()
        LOG.info("%s; report directory: %s", toolchain["pandoc_version"].splitlines()[0], self.report_dir)
        try:
            with tempfile.TemporaryDirectory(prefix="papper-benchmark-") as directory:
                temporary = Path(directory)
                for target in dict.fromkeys(self.settings.targets):
                    for mode in dict.fromkeys(self.settings.cache_modes):
                        for index in range(1, self.settings.warmups + 1):
                            self.sample(target, mode, index, True, temporary)
                        for index in range(1, self.settings.runs + 1):
                            self.sample(target, mode, index, False, temporary)
            self.summarize()
            self.report["status"] = "complete"
            self.write_markdown()
        except BaseException as exc:
            self.report.update(status="failed", error=str(exc))
            raise
        finally:
            self.save()
        LOG.info("Benchmark complete: %s", self.report_dir / "summary.md")


def main() -> int:
    """Configure concise progress logs and parse benchmark CLI/environment settings."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    try:
        CliApp.run(BenchmarkSettings)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
