"""Benchmark warm HTML HTTP conversions with real output-equivalence checks.

Run from the Papper checkout with:
  uv run python scripts/benchmark_html_server.py --manuscript template/manuscript.md
  uv run python scripts/benchmark_html_server.py --manuscript ../1-3d-mesh/manuscript.md

The script reads the original manuscript/style/resources, copies editable inputs
into a temporary project, starts real HTTP/native workers, and measures complete
local requests. It distinguishes unchanged HTML, changed prose, and changed
citation inputs. Every scenario's final output is compared to fresh Pandoc plus
Papper postprocessing, outside the timed interval. Raw samples and medians are
written into a new timestamped report directory; existing reports stay intact.

An optional old worker supplies a same-frontend comparison. Its input/output
files are kept inside the temporary project to bypass the legacy worker's
rejection of system-temp paths. This isolates native pipeline changes and is
explicitly recorded in the report; it does not benchmark the old path failure.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict

from pandoc_manuscript.commands import build
from pandoc_manuscript.commands import pandoc_server_runtime as runtime
from pandoc_manuscript.commands.setup import pandoc_tools_env
from pandoc_manuscript.html.build import prepare_html_metadata
from pandoc_manuscript.html.postprocess import postprocess_html_text
from pandoc_manuscript.runtime.metadata import write_markdown_without_yaml_header, write_pandoc_metadata
from pandoc_manuscript.runtime import paths

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger("papper.server-benchmark")
WORKER_NAME = "pmt-pandoc-worker.exe" if os.name == "nt" else "pmt-pandoc-worker"


class BenchmarkSettings(BaseSettings):
    """Typed command-line configuration for reproducible server samples."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    manuscript: Path = Field(default=str(ROOT / "template/manuscript.md"))  # Original input remains read-only
    project_dir: Path = Field(default=str(ROOT))  # Original working-directory style/resource context
    worker: Path = Field(default=str(runtime.PMT_TOOLS_BIN_DIR / WORKER_NAME))  # Optimized native executable
    baseline_worker: Path = Field(default=str(ROOT / "tmp/server-profile/worker-baseline.exe"))  # Optional pre-change executable
    compare: bool = Field(default=True)  # Skip an absent baseline and report the optimized service alone
    modes: list[Literal["exact"]] = Field(default=["exact"])  # Public HTTP conversions return complete documents
    scenarios: list[Literal["edit", "unchanged", "citation_edit"]] = Field(default=["edit", "unchanged", "citation_edit"])  # Distinct observable cache behavior
    runs: int = Field(default=15, ge=3)  # Timed samples with no outlier removal
    warmups: int = Field(default=2, ge=1)  # Exclude first resource loads and cache preparation
    output_dir: Path = Field(default=str(ROOT / "output/benchmarks/server"))  # A new report per invocation


class BenchmarkSession:
    """Own isolated project copies, live HTTP services, and persistent reports."""

    def __init__(self, settings: BenchmarkSettings) -> None:
        """Resolve the selected input/toolchain and allocate a new report directory."""
        self.settings = settings
        self.source = settings.manuscript.resolve(strict=True)
        self.project = settings.project_dir.resolve(strict=True)
        self.workers = {"optimized": settings.worker.resolve(strict=True)}
        if settings.compare and settings.baseline_worker.is_file():
            self.workers = {"baseline": settings.baseline_worker.resolve(), **self.workers}
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.report_dir = settings.output_dir.resolve() / timestamp
        self.report_dir.mkdir(parents=True, exist_ok=False)
        self.report = {
            "schema_version": 1, "status": "running", "started_at": timestamp,
            "input": str(self.source), "input_bytes": self.source.stat().st_size,
            "input_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "platform": platform.platform(), "python": platform.python_version(),
            "settings": settings.model_dump(mode="json"), "samples": [], "summary": [],
            "workers": {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for name, path in self.workers.items()},
            "boundary": "HTTP request, conversion/cache lookup, postprocess, response transfer; worker startup and CLI verification excluded",
            "baseline_note": "Same corrected HTTP frontend; legacy worker inputs/outputs stay inside the temporary project",
        }

    def save(self) -> None:
        """Persist raw observations so failures leave a reviewable partial report."""
        (self.report_dir / "results.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def prepare(self, temporary: Path) -> None:
        """Load original CLI metadata and copy only sources/style configuration."""
        os.chdir(self.project)
        os.environ.update(pandoc_tools_env())
        build.configure_manuscript(self.source, derive_project_name=True)
        self.effective = build.load_build_metadata()
        prepare_html_metadata(self.effective, self.source.stem)
        self.original = self.source.read_text(encoding="utf-8")
        candidates = re.findall(r"(?<!\w)@([\w:./-]+)", self.original)
        self.citation_id = next((value for value in candidates if not value.startswith(("fig:", "tbl:", "eq:", "sec:", "lst:"))), None)
        self.temporary = temporary
        paths.PAPPER_HOME_DIR = temporary / "state"
        self.roots = build.manuscript_resource_paths()
        self.report["pandoc_version"] = subprocess.run(["pandoc", "--version"], check=True, capture_output=True, text=True).stdout
        crossref = subprocess.run(["pandoc-crossref", "--version"], check=True, capture_output=True, text=True)
        self.report["crossref_version"] = crossref.stdout + crossref.stderr
        self.report["source_styles"] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in build.manuscript_style_paths()}

    def copy_project(self, label: str) -> tuple[Path, dict]:
        """Create a fresh worker project without copying large image directories."""
        project = self.temporary / label
        source_dir = project / "source"
        source_dir.mkdir(parents=True)
        source = source_dir / self.source.name
        source.write_text(self.original, encoding="utf-8")
        for origin, target in ((self.source.parent / "style.yml", source_dir / "style.yml"),
                               (self.project / "style.yml", project / "style.yml"),
                               (self.project / "pandoc-crossref.yaml", project / "pandoc-crossref.yaml")):
            if origin.is_file():
                shutil.copyfile(origin, target)
        metadata = project / "initial-metadata.yml"
        write_pandoc_metadata(self.effective.pandoc_metadata, metadata)
        config = {
            "project_dir": str(project), "resource_paths": list(map(str, self.roots)),
            "pandoc_args": ["--defaults", str(build.resource_path("pandoc/pandoc-html.yml")),
                            "--metadata-file", str(metadata), "--resource-path", os.pathsep.join(map(str, self.roots))],
            "pandoc_metadata": self.effective.pandoc_metadata,
            "metadata_sources": {"style_file": "style.yml", "project_name": self.source.stem},
            "worker_config": str(project / "worker.json"),
        }
        return source, config

    def reference_html(self, source: Path, config: dict) -> str:
        """Generate an independent CLI reference outside the measured interval."""
        output = source.parent / "reference.html"
        sanitized = write_markdown_without_yaml_header(source)
        try:
            subprocess.run(["pandoc", *config["pandoc_args"], str(sanitized or source), "-o", str(output)],
                           cwd=config["project_dir"], check=True, capture_output=True, text=True,
                           env=pandoc_tools_env(build.pandoc_filter_env(self.effective.pmt_settings)))
            return postprocess_html_text(output.read_text(encoding="utf-8"), pandoc_metadata=self.effective.pandoc_metadata)
        finally:
            if sanitized is not None:
                sanitized.unlink(missing_ok=True)

    def sample_worker(self, label: str, executable: Path) -> None:
        """Measure a real persistent HTTP connection against one native worker."""
        source, config = self.copy_project(label)
        os.environ["PMT_PANDOC_SERVER_WORKER_COMMAND"] = f'"{executable}"'
        original_temp = runtime.process_temp_dir

        def project_temp() -> Path:
            """Keep the legacy worker's private files within its authorized project."""
            return Path(config["project_dir"]) / "work"

        runtime.process_temp_dir = project_temp
        worker = runtime.PandocWorker(config, self.report_dir / f"{label}-worker.log")
        server = ThreadingHTTPServer(("127.0.0.1", 0), runtime.PmtHtmlRequestHandler)
        server.daemon_threads = True
        server.worker = worker
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=120)
        try:
            for mode in self.settings.modes:
                for scenario in self.settings.scenarios:
                    if scenario == "citation_edit" and self.citation_id is None:
                        continue
                    observations = []
                    for index in range(self.settings.warmups + self.settings.runs):
                        if scenario != "unchanged":
                            suffix = f"\n\nBenchmark prose edit {index}.\n"
                            if scenario == "citation_edit":
                                suffix += f"\nBenchmark citation [@{self.citation_id}, p. {index + 1}].\n"
                            source.write_text(self.original + suffix, encoding="utf-8")
                        started = time.perf_counter()
                        client.request("POST", "/convert/raw",
                                       body=json.dumps({"path": str(source)}).encode("utf-8"), headers={"Content-Type": "application/json"})
                        response = client.getresponse()
                        html = response.read().decode("utf-8")
                        elapsed = (time.perf_counter() - started) * 1000
                        if response.status != 200:
                            raise RuntimeError(f"{label} HTTP {response.status}: {html}")
                        stages = {name: float(value) for name, value in re.findall(r"([\w-]+);dur=([\d.]+)", response.getheader("Server-Timing", ""))}
                        row = {"worker": label, "mode": mode, "scenario": scenario, "run": index - self.settings.warmups + 1,
                               "http_ms": elapsed, "html_cache": response.getheader("X-PMT-Cache"),
                               "citation_cache": response.getheader("X-PMT-Citeproc-Cache"), "stages_ms": stages}
                        if index >= self.settings.warmups:
                            observations.append(row)
                            self.report["samples"].append(row)
                        LOG.info("%s %s %s %d: %.2f ms%s", label, mode, scenario, index + 1, elapsed,
                                 " (warmup)" if index < self.settings.warmups else "")
                    expected = self.reference_html(source, config)
                    if html != expected:
                        (self.report_dir / f"{label}-{mode}-{scenario}-actual.html").write_text(html, encoding="utf-8")
                        (self.report_dir / f"{label}-{mode}-{scenario}-expected.html").write_text(expected, encoding="utf-8")
                        raise RuntimeError(f"{label}/{mode}/{scenario} differs from fresh CLI output")
                    values = [row["http_ms"] for row in observations]
                    summary = {"worker": label, "mode": mode, "scenario": scenario, "n": len(values), "output_equal": True,
                               "median_ms": statistics.median(values), "mean_ms": statistics.mean(values),
                               "stdev_ms": statistics.stdev(values), "min_ms": min(values), "max_ms": max(values),
                               "html_hits": sum(row["html_cache"] == "hit" for row in observations),
                               "citation_hits": sum(row["citation_cache"] == "hit" for row in observations)}
                    self.report["summary"].append(summary)
                    self.save()
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            worker.close()
            runtime.process_temp_dir = original_temp

    def markdown(self) -> None:
        """Write measurement boundaries, medians, and same-input improvements."""
        lines = ["# Warm HTML server benchmark", "", f"Input: `{self.source}` ({self.report['input_bytes']} bytes)",
                 "", self.report["boundary"] + ".", "", self.report["baseline_note"] + ".",
                 "", "Warmup samples are excluded; all timed samples are retained. Original inputs are read-only. "
                 "Final HTML for every scenario matched a fresh CLI conversion.", "",
                 "| Worker | Mode | Scenario | N | Median ms | Mean ± SD ms | HTML hits | Citation hits |",
                 "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
        for row in self.report["summary"]:
            lines.append(f"| {row['worker']} | {row['mode']} | {row['scenario']} | {row['n']} | {row['median_ms']:.3f} | "
                         f"{row['mean_ms']:.3f} ± {row['stdev_ms']:.3f} | {row['html_hits']} | {row['citation_hits']} |")
        lines += ["", "| Mode | Scenario | Reduction in median request time |", "| --- | --- | ---: |"]
        for row in self.report["summary"]:
            if row["worker"] != "optimized":
                continue
            baseline = next((item for item in self.report["summary"] if item["worker"] == "baseline" and
                             item["mode"] == row["mode"] and item["scenario"] == row["scenario"]), None)
            if baseline is not None:
                reduction = (1 - row["median_ms"] / baseline["median_ms"]) * 100
                lines.append(f"| {row['mode']} | {row['scenario']} | {reduction:.1f}% |")
        lines += ["", "Changing citation inputs triggers fresh citation evaluation; prose edits reuse evaluation while "
                  "reapplying note/punctuation/bibliography mutations to the current document.", "", "Raw samples, stages, hashes, and tool versions: `results.json`.", ""]
        (self.report_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    def run(self) -> None:
        """Execute sequential comparisons and preserve success/failure evidence."""
        self.save()
        try:
            with tempfile.TemporaryDirectory(prefix="papper-server-benchmark-") as directory:
                self.prepare(Path(directory))
                for label, executable in self.workers.items():
                    self.sample_worker(label, executable)
            self.report["status"] = "complete"
            self.markdown()
        except BaseException as exc:
            self.report.update(status="failed", error=str(exc))
            raise
        finally:
            self.save()
        LOG.info("Report: %s", self.report_dir / "summary.md")


def main() -> None:
    """Parse validated settings and log progress throughout the benchmark."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    BenchmarkSession(CliApp.run(BenchmarkSettings)).run()


if __name__ == "__main__":
    main()
