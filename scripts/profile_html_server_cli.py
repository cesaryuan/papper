"""Locate warm HTML CLI overhead with real processes and isolated projects.

Run `uv run python scripts/profile_html_server_cli.py --runs 10`. The experiment
interleaves the public uv/Papper command, a fresh Python process executing the
same CLI with timing wrappers, and direct HTTP requests. Each route owns a warm
Server, receives identical edits, and must produce identical HTML. Existing
sources, production files, and the Git index are preserved. Extra cProfile and
importtime runs explain hot spots but never enter performance medians.
"""

from __future__ import annotations

from time import perf_counter

ENTRY_CLOCK = perf_counter()

import os
import sys

if os.environ.get("PAPPER_PROFILE_CHILD") == "1":
    from cli_profile_probe import run
    raise SystemExit(run(sys.argv[1:], ENTRY_CLOCK))

import hashlib
import http.client
import json
import logging
import random
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, CliApp, SettingsConfigDict

import benchmark_html_server as benchmark
from pandoc_manuscript.commands.pandoc_server import _stop_pid
from pandoc_manuscript.runtime.paths import project_state_dir

LOG = logging.getLogger("papper.cli-profile")


class ProfileSettings(BaseSettings):
    """Select read-only inputs and independent warm profiling rounds."""

    model_config = SettingsConfigDict(cli_kebab_case=True, cli_implicit_flags=True)

    manuscripts: list[Path] = Field(default=[str(benchmark.ROOT / "template/manuscript.md"), str(benchmark.ROOT.parent / "1-3d-mesh/manuscript.md")])  # Original manuscripts stay read-only
    runs: int = Field(default=10, ge=3)  # Formal paired samples, with no outlier removal
    warmups: int = Field(default=2, ge=1)  # Prime services and citation/result caches
    seed: int = Field(default=20261001)  # Reproducible interleaved route order
    output_dir: Path = Field(default=str(benchmark.ROOT / "output/benchmarks/server-cli-profile"))  # Durable raw observations and reports
    startup_runs: int = Field(default=10, ge=3)  # Separate uv/empty-Python launcher control
    compare_baseline: bool = Field(default=False)  # Interleave the saved pre-optimization CLI
    baseline_src: Path = Field(default=str(benchmark.ROOT.parent / "papper-cli-client-before-20261001/src"))  # Preserved staged implementation outside test discovery


class CliProfileExperiment:
    """Compare complete CLI invocations with observed HTTP and function stages."""

    def __init__(self, settings: ProfileSettings) -> None:
        """Create a report and snapshot the pre-existing staged tree."""
        self.settings = settings
        self.random = random.Random(settings.seed)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.directory = settings.output_dir.resolve() / stamp
        self.directory.mkdir(parents=True)
        self.index = subprocess.run(["git", "write-tree"], cwd=benchmark.ROOT, check=True, capture_output=True, text=True).stdout.strip()
        self.report: dict[str, Any] = {"status": "running", "settings": settings.model_dump(mode="json"),
                                       "index_tree": self.index, "samples": [], "diagnostics": [], "output_checks": 0}

    def startup_control(self) -> None:
        """Compare empty interpreters to isolate the added uv launch cost."""
        rows = []
        commands = {"python_only": [sys.executable, "-c", "pass"],
                    "uv_python": ["uv", "run", "--project", str(benchmark.ROOT), "python", "-c", "pass"]}
        # Scoop's Windows executable shim adds another process boundary. When
        # its adjacent metadata identifies the real executable, measure that
        # route independently rather than charging all shim cost to uv itself.
        uv_path = Path(shutil.which("uv") or "uv")
        shim = uv_path.with_suffix(".shim")
        if os.name == "nt" and shim.is_file():
            match = re.search(r'^path\s*=\s*"([^"]+)"', shim.read_text(encoding="utf-8"), re.MULTILINE)
            if match and Path(match[1]).is_file():
                commands["uv_binary_python"] = [match[1], "run", "--project", str(benchmark.ROOT), "python", "-c", "pass"]
        for index in range(self.settings.startup_runs + self.settings.warmups):
            order = list(commands)
            self.random.shuffle(order)
            for label in order:
                started = perf_counter()
                subprocess.run(commands[label], cwd=benchmark.ROOT, check=True, capture_output=True)
                elapsed = (perf_counter() - started) * 1000
                if index >= self.settings.warmups:
                    rows.append({"variant": label, "round": index - self.settings.warmups + 1, "elapsed_ms": elapsed})
        self.report["startup_control"] = rows
        self.save()

    def save(self) -> None:
        """Publish raw evidence after each paired round and on failures."""
        (self.directory / "results.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def free_port() -> int:
        """Choose an unused local port for one private project's service."""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def command(self, source: Path, output: Path, port: int, roots: tuple[Path, ...], *, probe: bool = False, importtime: bool = False) -> list[str]:
        """Build the same public CLI arguments, optionally through the probe."""
        entry = ["python", *( ["-X", "importtime"] if importtime else []), str(Path(__file__).resolve())] if probe else ["papper"]
        return ["uv", "run", "--project", str(benchmark.ROOT), *entry, "build", "html", str(source),
                "-o", str(output), "--resource-path", os.pathsep.join(map(str, roots)),
                "--start-server", "--server-port", str(port)]

    def run_input(self, manuscript: Path, temporary: Path) -> None:
        """Profile three independently warm routes and verify paired HTML."""
        name = manuscript.parent.name + "-" + manuscript.stem
        session = benchmark.BenchmarkSession(benchmark.BenchmarkSettings(manuscript=manuscript, compare=False,
                                                                         output_dir=self.directory / "references"))
        original_home = benchmark.paths.PAPPER_HOME_DIR
        session.prepare(temporary)
        # Child processes use the normal user state home; keep parent lookups in
        # sync so cleanup cannot confuse experimental and historical services.
        benchmark.paths.PAPPER_HOME_DIR = original_home
        labels = ["public_cli", "timed_cli", "direct_http"]
        if self.settings.compare_baseline:
            if not (self.settings.baseline_src / "pandoc_manuscript/cli.py").is_file():
                raise FileNotFoundError(self.settings.baseline_src)
            labels.append("baseline_cli")
        variants = {label: session.copy_project(label) for label in labels}
        ports = {label: self.free_port() for label in variants}
        environment = {**os.environ, "PMT_PANDOC_SERVER_WORKER_COMMAND": f'"{session.settings.worker.resolve()}"'}
        clients: dict[str, http.client.HTTPConnection] = {}
        environments = {label: environment.copy() for label in variants}
        if "baseline_cli" in environments:
            # Only Python client code changes; the native worker, uv launcher,
            # dependencies and original read-only input resources stay shared.
            environments["baseline_cli"]["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(self.settings.baseline_src.resolve()), environment.get("PYTHONPATH"))),
            )
        try:
            for label, (source, config) in variants.items():
                initialized = subprocess.run(self.command(source, Path(config["project_dir"]) / "cli.html", ports[label], session.roots),
                                             cwd=config["project_dir"], env=environments[label], capture_output=True, timeout=120)
                if initialized.returncode:
                    raise RuntimeError(f"{label} service initialization failed: {initialized.stderr.decode('utf-8', errors='replace')}")
            clients["direct_http"] = http.client.HTTPConnection("127.0.0.1", ports["direct_http"], timeout=120)
            for scenario in ("edit", "unchanged"):
                for index in range(self.settings.warmups + self.settings.runs):
                    suffix = f"\n\nCLI profile fresh prose {index}.\n" if scenario == "edit" else "\n\nCLI profile unchanged source.\n"
                    order = list(variants)
                    self.random.shuffle(order)
                    outputs = []
                    for position, label in enumerate(order):
                        source, config = variants[label]
                        source.write_text(session.original + suffix, encoding="utf-8")
                        output = Path(config["project_dir"]) / "cli.html"
                        row: dict[str, Any] = {"input": name, "scenario": scenario, "variant": label,
                                               "round": index - self.settings.warmups + 1, "position": position}
                        if label == "direct_http":
                            started = perf_counter()
                            client = clients[label]
                            client.request("POST", "/convert/raw", body=json.dumps({"path": str(source)}), headers={"Content-Type": "application/json"})
                            response = client.getresponse()
                            content = response.read()
                            row["elapsed_ms"] = (perf_counter() - started) * 1000
                            if response.status != 200:
                                raise RuntimeError(content.decode("utf-8", errors="replace"))
                            row["server_timing"] = response.getheader("Server-Timing")
                            row["html_cache"] = response.getheader("X-PMT-Cache")
                        else:
                            child_environment = environments[label].copy()
                            trace = self.directory / f"{name}-{scenario}-{index}-{label}.json"
                            if label == "timed_cli":
                                child_environment.update(PAPPER_PROFILE_CHILD="1", PAPPER_PROFILE_TRACE=str(trace))
                            started = perf_counter()
                            completed = subprocess.run(self.command(source, output, ports[label], session.roots, probe=label == "timed_cli"),
                                                       cwd=config["project_dir"], env=child_environment, capture_output=True,
                                                       text=True, encoding="utf-8", check=True, timeout=120)
                            finished = perf_counter()
                            row["elapsed_ms"] = (finished - started) * 1000
                            row["stdout"], row["stderr"] = completed.stdout, completed.stderr
                            content = output.read_bytes()
                            if label == "timed_cli":
                                row["trace"] = json.loads(trace.read_text(encoding="utf-8"))
                                exit_clock = json.loads(trace.with_suffix(".exit.json").read_text(encoding="utf-8"))["exit_clock"]
                                row["launcher_entry_ms"] = (row["trace"]["entry_clock"] - started) * 1000
                                row["post_main_cleanup_ms"] = (exit_clock - row["trace"]["main_finished_clock"]) * 1000
                                row["final_process_exit_ms"] = (finished - exit_clock) * 1000
                                if min(row[key] for key in ("launcher_entry_ms", "post_main_cleanup_ms", "final_process_exit_ms")) < 0:
                                    raise RuntimeError("Profiling needs a shared monotonic clock across processes")
                        row["html_sha256"] = hashlib.sha256(content).hexdigest()
                        # The Windows CLI writer uses CRLF while the HTTP body
                        # contains LF; compare decoded HTML under the same newline
                        # policy rather than treating transport line endings as a
                        # document difference.
                        outputs.append(content.decode("utf-8").replace("\r\n", "\n"))
                        if label != "direct_http":
                            connection = json.loads((project_state_dir(Path(config["project_dir"])) / "pandoc-server.json").read_text(encoding="utf-8"))
                            row["server_pid"], row["server_started_at"] = connection["pid"], connection["started_at"]
                        if index >= self.settings.warmups:
                            self.report["samples"].append(row)
                    if len(set(outputs)) != 1:
                        raise RuntimeError(f"Profile routes disagree: {name}/{scenario}/{index}")
                    self.report["output_checks"] += len(outputs)
                    self.save()
                    LOG.info("%s %s round %d/%d complete", name, scenario, index + 1, self.settings.warmups + self.settings.runs)
            source, config = variants["timed_cli"]
            for mode in ("cprofile", "importtime"):
                source.write_text(session.original + f"\n\nDiagnostic {mode} fresh prose.\n", encoding="utf-8")
                artifact = self.directory / f"{name}-{mode}.json"
                child_environment = {**environment, "PAPPER_PROFILE_CHILD": "1", "PAPPER_PROFILE_TRACE": str(artifact),
                                     "PAPPER_PROFILE_MODE": "cprofile" if mode == "cprofile" else "timings"}
                completed = subprocess.run(self.command(source, Path(config["project_dir"]) / "cli.html", ports["timed_cli"], session.roots,
                                                        probe=True, importtime=mode == "importtime"),
                                           cwd=config["project_dir"], env=child_environment, capture_output=True,
                                           text=True, encoding="utf-8", check=True, timeout=120)
                if mode == "importtime":
                    artifact.with_suffix(".imports.txt").write_text(completed.stderr, encoding="utf-8")
                self.report["diagnostics"].append({"input": name, "mode": mode, "trace": str(artifact)})
        finally:
            for client in clients.values():
                client.close()
            for _, config in variants.values():
                state = project_state_dir(Path(config["project_dir"])) / "pandoc-server.json"
                if state.is_file():
                    _stop_pid(int(json.loads(state.read_text(encoding="utf-8"))["pid"]))

    def summarize(self) -> None:
        """Aggregate nested stages using exclusive spans, never adding parents."""
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in self.report["samples"]:
            groups[(row["input"], row["scenario"], row["variant"])].append(row)
        lines = ["# Warm HTML CLI profile", "", "Independent processes; medians exclude warmups and cProfile/importtime diagnostics. No samples removed.", "",
                 "| Input | Scenario | Route | N | Median ms |", "| --- | --- | --- | ---: | ---: |"]
        for (name, scenario, label), rows in groups.items():
            if label != "direct_http" and len({row["server_started_at"] for row in rows}) != 1:
                raise RuntimeError(f"Expected a reused warm service: {name}/{scenario}/{label}")
            lines.append(f"| {name} | {scenario} | {label} | {len(rows)} | {statistics.median(row['elapsed_ms'] for row in rows):.3f} |")
        if self.settings.compare_baseline:
            comparisons = []
            lines.extend(["", "## Saved CLI versus optimized CLI", "",
                          "Same uv launcher, native worker and resources; independent warm services, shuffled within each round.", "",
                          "| Input | Scenario | Before ms | After ms | Reduction | Paired savings ms |",
                          "| --- | --- | ---: | ---: | ---: | ---: |"])
            for name, scenario in dict.fromkeys((key[0], key[1]) for key in groups):
                before = {row["round"]: row["elapsed_ms"] for row in groups[(name, scenario, "baseline_cli")]}
                after = {row["round"]: row["elapsed_ms"] for row in groups[(name, scenario, "public_cli")]}
                old_median, new_median = statistics.median(before.values()), statistics.median(after.values())
                change = {"input": name, "scenario": scenario, "before_ms": old_median, "after_ms": new_median,
                          "reduction_percent": (1 - new_median / old_median) * 100,
                          "paired_savings_ms": statistics.median(before[index] - after[index] for index in before)}
                comparisons.append(change)
                lines.append(f"| {name} | {scenario} | {old_median:.3f} | {new_median:.3f} | {change['reduction_percent']:.1f}% | {change['paired_savings_ms']:.3f} |")
            self.report["cli_comparison"] = comparisons
        summaries = []
        for (name, scenario, label), rows in groups.items():
            if label != "timed_cli":
                continue
            values: dict[str, list[float]] = defaultdict(list)
            for row in rows:
                trace = row["trace"]
                stages = {"bootstrap": trace["bootstrap_ms"], "cli_import": trace["cli_import_ms"],
                          "probe_install": trace["probe_install_ms"], "launcher_entry": row["launcher_entry_ms"],
                          "post_main_cleanup": row["post_main_cleanup_ms"], "final_process_exit": row["final_process_exit_ms"]}
                for event in trace["events"]:
                    stages[event["label"]] = stages.get(event["label"], 0.0) + event["exclusive_ms"]
                # The spans telescope to main's wall time; record sub-millisecond
                # uncovered timer bookends instead of assigning them to a stage.
                stages["timing_bookends"] = trace["child_ms"] - sum(value for key, value in stages.items() if key not in {"launcher_entry", "post_main_cleanup", "final_process_exit"})
                for key, value in stages.items():
                    values[key].append(value)
                if abs(sum(stages.values()) - row["elapsed_ms"]) > 0.01:
                    raise RuntimeError("Exclusive profile stages do not account for the invocation")
            summary = {"input": name, "scenario": scenario, "stages_ms": {key: statistics.median(samples) for key, samples in values.items()}}
            summaries.append(summary)
            lines.extend(["", f"## {name}: {scenario}", "", "| Exclusive stage | Median ms |", "| --- | ---: |"])
            for key, value in sorted(summary["stages_ms"].items(), key=lambda item: item[1], reverse=True):
                lines.append(f"| {key} | {value:.3f} |")
            import_values: dict[str, list[float]] = defaultdict(list)
            for row in rows:
                imports = {"package_init": row["trace"]["root_import_ms"]}
                for event in row["trace"]["direct_imports"]:
                    imports[event["module"]] = imports.get(event["module"], 0.0) + event["elapsed_ms"]
                imports["cli_module_other"] = row["trace"]["cli_import_ms"] - sum(imports.values())
                for key, value in imports.items():
                    import_values[key].append(value)
            summary["direct_imports_ms"] = {key: statistics.median(samples) for key, samples in import_values.items()}
            lines.extend(["", "Direct CLI imports in actual source order; dependencies are charged to the first importer, so these are not standalone module costs.", "",
                          "| Direct import | Median ms |", "| --- | ---: |"])
            for key, value in sorted(summary["direct_imports_ms"].items(), key=lambda item: item[1], reverse=True):
                lines.append(f"| {key} | {value:.3f} |")
        self.report["stage_summary"] = summaries
        lines.extend(["", "Stage medians need not sum to the median total. launcher_entry includes uv, OS/Python startup and the probe's initial time import; post_main_cleanup includes report export and project atexit cleanup; final_process_exit includes final Python/uv exit and the exit-clock export.",
                      "HTTP spans end at receipt of response headers; remaining response decoding/output work is in server_client_other and write_html.",
                      f"All {self.report['output_checks']} paired output checks passed; cProfile/importtime samples are explanatory only."])
        lines.extend(["", "## Empty interpreter startup control", "", "| Route | Median ms |", "| --- | ---: |"])
        for label in dict.fromkeys(row["variant"] for row in self.report["startup_control"]):
            values = [row["elapsed_ms"] for row in self.report["startup_control"] if row["variant"] == label]
            lines.append(f"| {label} | {statistics.median(values):.3f} |")
        (self.directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.report["status"] = "complete"
        self.save()


def main() -> None:
    """Run isolated profiling and verify that the staged implementation survived."""
    settings = CliApp.run(ProfileSettings)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    experiment = CliProfileExperiment(settings)
    try:
        experiment.startup_control()
        with tempfile.TemporaryDirectory(prefix="papper-cli-profile-") as temporary:
            for index, manuscript in enumerate(settings.manuscripts):
                experiment.run_input(manuscript.resolve(), Path(temporary) / str(index))
        experiment.summarize()
    finally:
        experiment.save()
        current = subprocess.run(["git", "write-tree"], cwd=benchmark.ROOT, check=True, capture_output=True, text=True).stdout.strip()
        if current != experiment.index:
            raise RuntimeError("The staged implementation changed during profiling")
    LOG.info("CLI profile report: %s", experiment.directory / "summary.md")


if __name__ == "__main__":
    main()
