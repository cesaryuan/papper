"""Instrument one fresh Papper invocation without changing production files.

The parent profile_html_server_cli.py starts this probe in a new Python process.
Selected function wrappers record nested wall times, subtract nested spans, and
capture the real HTTP response's Server-Timing headers. Optional cProfile and
Python importtime runs are diagnostic samples, excluded from benchmark medians.
Papper receives its original arguments and retains its normal update checks.
"""

from __future__ import annotations

import functools
import builtins
import atexit
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


class InvocationTrace:
    """Record inclusive and exclusive spans on the synchronous CLI thread."""

    def __init__(self) -> None:
        """Allocate an event list and the active wrapper stack."""
        self.events: list[dict[str, Any]] = []
        self.stack: list[dict[str, Any]] = []

    def start(self, label: str) -> dict[str, Any]:
        """Begin a span, remembering its parent to avoid double counting."""
        event = {"label": label, "started": time.perf_counter(), "children_ms": 0.0,
                 "parent": self.stack[-1]["index"] if self.stack else None, "index": len(self.events)}
        self.events.append(event)
        self.stack.append(event)
        return event

    def finish(self, event: dict[str, Any]) -> None:
        """Close a span and charge its inclusive duration to its parent."""
        elapsed = (time.perf_counter() - event.pop("started")) * 1000
        self.stack.pop()
        event["inclusive_ms"] = elapsed
        event["exclusive_ms"] = elapsed - event.pop("children_ms")
        if self.stack:
            self.stack[-1]["children_ms"] += elapsed

    def wrapper(self, function: Any, label: str) -> Any:
        """Time a callable while retaining exceptions and return semantics."""
        @functools.wraps(function)
        def measured(*args: Any, **kwargs: Any) -> Any:
            """Execute the original callable inside a balanced timing span."""
            event = self.start(label)
            try:
                return function(*args, **kwargs)
            finally:
                self.finish(event)
        return measured

    def patch_aliases(self, module_name: str, name: str, label: str) -> None:
        """Replace already imported aliases so lazy local imports see the wrapper."""
        original = getattr(sys.modules[module_name], name)
        measured = self.wrapper(original, label)
        for module in tuple(sys.modules.values()):
            if module is not None and getattr(module, "__name__", "").startswith("pandoc_manuscript"):
                for attribute, value in tuple(vars(module).items()):
                    if value is original:
                        setattr(module, attribute, measured)

    def install(self, cli: Any, cli_model: Any) -> None:
        """Instrument stage boundaries and real local HTTP requests."""
        targets = [
            ("commands.build", "run_build_command", "build_dispatch"),
            ("html.build", "build_html", "html_other"),
            ("commands.build", "load_build_metadata", "load_metadata"),
            ("html.build", "prepare_html_metadata", "prepare_html_metadata"),
            ("commands.build", "style_metadata_args", "generate_metadata_yaml"),
            ("commands.build", "manuscript_resource_paths", "resource_paths"),
            ("commands.pandoc_server", "write_pmt_server_config", "write_server_config"),
            ("commands.pandoc_server", "ensure_pandoc_server", "ensure_server"),
            ("commands.pandoc_server", "build_with_pandoc_server", "server_client_other"),
            ("commands.setup.pandoc_tools", "pandoc_tools_env", "tools_environment"),
            ("commands.setup.pandoc_tools", "ensure_pandoc_tools", "validate_tools"),
            ("commands.setup.pandoc_tools", "subprocess_run_version", "tool_version_subprocess"),
            ("runtime.update_check", "notify_and_schedule_update_check", "update_check"),
        ]
        for module_name, name, label in targets:
            qualified = "pandoc_manuscript." + module_name
            if qualified in sys.modules:
                self.patch_aliases(qualified, name, label)
        cli_model.cli_cmd = self.wrapper(cli_model.cli_cmd, "cli_dispatch")
        cli.BuildCommandSettings.run = self.wrapper(cli.BuildCommandSettings.run, "build_settings_run")

        import urllib.request
        original_open = urllib.request.OpenerDirector.open

        @functools.wraps(original_open)
        def measured_open(opener: Any, fullurl: Any, *args: Any, **kwargs: Any) -> Any:
            """Measure request-to-response-header time and keep server evidence."""
            url = getattr(fullurl, "full_url", str(fullurl))
            label = "http_convert" if url.endswith("/convert/raw") else "http_version" if url.endswith("/version") else "http_other"
            event = self.start(label)
            event["url"] = url
            try:
                response = original_open(opener, fullurl, *args, **kwargs)
                event["server_timing"] = response.headers.get("Server-Timing", "")
                event["html_cache"] = response.headers.get("X-PMT-Cache")
                event["citation_cache"] = response.headers.get("X-PMT-Citeproc-Cache")
                return response
            finally:
                self.finish(event)
        urllib.request.OpenerDirector.open = measured_open
        urllib.request.build_opener = self.wrapper(urllib.request.build_opener, "http_client_init")
        import ssl
        # urllib uses ssl's pre-bound _create_default_https_context alias;
        # patch every alias to the same function so that path is measured too.
        original_context = ssl.create_default_context
        measured_context = self.wrapper(original_context, "tls_context_init")
        for attribute, value in tuple(vars(ssl).items()):
            if value is original_context:
                setattr(ssl, attribute, measured_context)

        original_write = Path.write_text

        @functools.wraps(original_write)
        def measured_write(path: Path, *args: Any, **kwargs: Any) -> Any:
            """Measure final output writes separately from generated YAML."""
            event = self.start("write_html" if path.suffix == ".html" else "write_intermediate")
            try:
                return original_write(path, *args, **kwargs)
            finally:
                self.finish(event)
        Path.write_text = measured_write


def run(argv: list[str], entry_clock: float) -> int:
    """Import and run the normal CLI, exporting raw spans after measurement."""
    before_import = time.perf_counter()
    artifact = Path(os.environ["PAPPER_PROFILE_TRACE"])

    def export_exit_clock() -> None:
        """Mark late exit after project cleanup, using the shared Windows clock."""
        clock = time.perf_counter()
        # Registration precedes Papper imports, so its cleanup callbacks run
        # first under atexit's reverse-registration order.
        artifact.with_suffix(".exit.json").write_text(json.dumps({"exit_clock": clock}), encoding="utf-8")
    atexit.register(export_exit_clock)
    mode = os.environ.get("PAPPER_PROFILE_MODE", "timings")
    profiler = None
    if mode == "cprofile":
        import cProfile
        profiler = cProfile.Profile()
    started_import = time.perf_counter()
    if profiler is not None:
        profiler.enable()
    import pandoc_manuscript
    root_imported = time.perf_counter()
    direct_imports = []
    original_import = builtins.__import__

    def measured_import(name: str, *args: Any, **kwargs: Any) -> Any:
        """Record direct CLI imports without summing nested import trees."""
        caller = sys._getframe(1).f_globals.get("__name__")
        if caller != "pandoc_manuscript.cli":
            return original_import(name, *args, **kwargs)
        started = time.perf_counter()
        try:
            return original_import(name, *args, **kwargs)
        finally:
            direct_imports.append({"module": name, "elapsed_ms": (time.perf_counter() - started) * 1000})
    builtins.__import__ = measured_import
    try:
        import pandoc_manuscript.cli as cli
        # Resolve the selected model inside the import phase. Accessing the
        # legacy PmtCli export would eagerly load every command for this probe.
        cli_model = cli._get_cli_model(argv) if hasattr(cli, "_get_cli_model") else cli.PmtCli
    finally:
        builtins.__import__ = original_import
    if profiler is not None:
        profiler.disable()
    after_import = time.perf_counter()
    trace = InvocationTrace()
    trace.install(cli, cli_model)
    started_main = time.perf_counter()
    main_event = trace.start("parse_cli_and_main_other")
    if profiler is not None:
        profiler.enable()
    try:
        code = cli.main(argv)
    finally:
        if profiler is not None:
            profiler.disable()
        trace.finish(main_event)
    finished_main = time.perf_counter()
    payload = {"mode": mode, "exit_code": code, "events": trace.events,
               "entry_clock": entry_clock, "main_finished_clock": finished_main,
               "bootstrap_ms": (started_import - entry_clock) * 1000,
               "cli_import_ms": (after_import - started_import) * 1000,
               "root_import_ms": (root_imported - started_import) * 1000,
               "direct_imports": direct_imports,
               "probe_install_ms": (started_main - after_import) * 1000,
               "cli_main_ms": (finished_main - started_main) * 1000,
               "child_ms": (finished_main - entry_clock) * 1000,
               "probe_module_ms": (before_import - entry_clock) * 1000}
    artifact.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if profiler is not None:
        import pstats
        profiler.dump_stats(str(artifact.with_suffix(".pstats")))
        with artifact.with_suffix(".profile.txt").open("w", encoding="utf-8") as output:
            pstats.Stats(profiler, stream=output).sort_stats("cumulative").print_stats(70)
    return int(code)
