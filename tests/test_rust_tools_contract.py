"""Exercise native tool diagnostics and detached update refresh at real process boundaries."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

from native_support import native_pandoc_executable
from test_build_snapshots import ROOT


def test_native_update_does_not_hold_captured_cli_pipes(
    rust_executable: Path, tmp_path: Path
) -> None:
    """Catch inherited Windows pipes that make a completed CLI wait for network timeout."""
    cache = tmp_path / "update.json"
    previous = {"latest_version": "99.0rc1", "checked_at": 1000, "attempted_at": 1000}
    cache.write_text(json.dumps(previous), encoding="utf-8")
    received = threading.Event()
    release = threading.Event()
    requests: list[str] = []
    errors: list[BaseException] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(10)

    def stalled_proxy() -> None:
        """Accept a real HTTPS tunnel and deliberately leave the update request unanswered."""
        try:
            with listener:
                connection, _ = listener.accept()
                with connection:
                    requests.append(connection.recv(4096).decode("ascii", "replace").splitlines()[0])
                    received.set()
                    release.wait(10)
        except BaseException as error:
            errors.append(error)
            received.set()

    worker = threading.Thread(target=stalled_proxy, daemon=True)
    worker.start()
    environment = {
        **os.environ,
        "PAPPER_RESOURCE_ROOT": str(ROOT),
        "PAPPER_UPDATE_CACHE": str(cache),
        "HTTPS_PROXY": f"http://127.0.0.1:{listener.getsockname()[1]}",
    }
    environment.pop("PAPPER_DISABLE_UPDATE_CHECK", None)
    command = [str(rust_executable), "setup", "--force"]
    process = subprocess.Popen(command, cwd=ROOT / "template", env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        assert process.wait(timeout=15) == 0
        assert received.wait(10), "Detached update worker did not reach the local proxy"
        assert not errors, errors
        # The CLI has exited, but the real worker is still blocked on network.
        # EOF must arrive now, not after its two-second request timeout.
        stdout, stderr = process.communicate(timeout=0.75)
        assert "no tool downloads needed" in stdout
        assert "99.0rc1 is available" in stderr
        assert requests == ["CONNECT pypi.org:443 HTTP/1.1"]
    finally:
        release.set()
        worker.join(timeout=12)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    deadline = time.monotonic() + 5
    refreshed = previous
    while time.monotonic() < deadline:
        refreshed = json.loads(cache.read_text(encoding="utf-8"))
        if refreshed["attempted_at"] > 1000:
            break
        time.sleep(0.02)
    assert refreshed["latest_version"] == previous["latest_version"]
    assert refreshed["checked_at"] == previous["checked_at"]
    assert refreshed["attempted_at"] > previous["attempted_at"]


def test_native_doctor_reports_missing_project_files(
    rust_executable: Path, tmp_path: Path
) -> None:
    """Return failure for an incomplete manuscript while still diagnosing native tools."""
    environment = {**os.environ, "PAPPER_RESOURCE_ROOT": str(ROOT), "PAPPER_DISABLE_UPDATE_CHECK": "1"}
    result = subprocess.run([str(rust_executable), "doctor"], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 1
    assert "[OK] pandoc --version:" in result.stdout
    assert "[OK] pandoc-crossref --version:" in result.stdout
    assert "[ERROR] project manuscript.md:" in result.stdout
    assert "[ERROR] project style.yml:" in result.stdout


@pytest.fixture
def doctor_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Isolate editable defaults while reusing the real retained native engine."""
    engine = native_pandoc_executable()
    assert engine is not None, "Doctor integration requires the native Pandoc engine"
    resources = tmp_path / "resources"
    shutil.copytree(ROOT / "pandoc/filters", resources / "pandoc/filters")
    for name in ["pandoc-docx.yml", "pandoc-html.yml"]:
        shutil.copy2(ROOT / "pandoc" / name, resources / "pandoc" / name)
    record = resources / ".pmt/pandoc-worker/current.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"executable": str(engine)}), encoding="utf-8")
    (tmp_path / "manuscript.md").write_text("Diagnostic manuscript.\n", encoding="utf-8")
    (tmp_path / "style.yml").write_text("title: Diagnostic manuscript\n", encoding="utf-8")
    environment = {
        **os.environ,
        "PAPPER_RESOURCE_ROOT": str(resources),
        "PAPPER_HOME": str(tmp_path / "state"),
        "PAPPER_DISABLE_UPDATE_CHECK": "1",
    }
    return resources, environment


@pytest.mark.parametrize("defaults_name", ["pandoc-docx.yml", "pandoc-html.yml"])
@pytest.mark.parametrize("filter_key", ["filters", "lua-filter"])
def test_native_doctor_follows_configured_filter_changes(
    rust_executable: Path, tmp_path: Path, doctor_project: tuple[Path, dict[str, str]],
    defaults_name: str, filter_key: str,
) -> None:
    """Catch stale diagnostics after adding, repairing, moving, or removing a filter."""
    resources, environment = doctor_project
    defaults_path = resources / "pandoc" / defaults_name
    defaults = yaml.safe_load(defaults_path.read_text(encoding="utf-8"))

    def diagnose() -> subprocess.CompletedProcess[str]:
        """Run the public command against the current isolated resources."""
        return subprocess.run([str(rust_executable), "doctor"], cwd=tmp_path, env=environment,
                              capture_output=True, text=True, encoding="utf-8", timeout=15)

    baseline = diagnose()
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    configured = defaults.setdefault(filter_key, [])
    configured.append("${.}/filters/shared/diagnostic_extension.lua")
    defaults_path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")
    missing = diagnose()
    assert missing.returncode == 1
    assert any("[ERROR] papper filter:" in line and "diagnostic_extension.lua" in line
               for line in missing.stdout.splitlines()), missing.stdout

    extension = resources / "pandoc/filters/shared/diagnostic_extension.lua"
    extension.write_text("-- Empty filter used to exercise runtime diagnostics.\n", encoding="utf-8")
    repaired = diagnose()
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr

    moved = extension.with_name("relocated_extension.lua")
    extension.rename(moved)
    configured[-1] = "${.}/filters/shared/relocated_extension.lua"
    defaults_path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")
    relocated = diagnose()
    assert relocated.returncode == 0, relocated.stdout + relocated.stderr
    assert "diagnostic_extension.lua" not in relocated.stdout

    moved.unlink()
    configured.pop()
    defaults_path.write_text(yaml.safe_dump(defaults, sort_keys=False), encoding="utf-8")
    removed = diagnose()
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert "relocated_extension.lua" not in removed.stdout


@pytest.mark.parametrize("invalid_defaults", ["filters: [", "filters: 42\n"])
def test_native_doctor_reports_invalid_defaults(
    rust_executable: Path, tmp_path: Path, doctor_project: tuple[Path, dict[str, str]],
    invalid_defaults: str,
) -> None:
    """Reject unreadable filter configuration while still reporting project diagnostics."""
    resources, environment = doctor_project
    defaults_path = resources / "pandoc/pandoc-docx.yml"
    defaults_path.write_text(invalid_defaults, encoding="utf-8")
    result = subprocess.run([str(rust_executable), "doctor"], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 1
    assert "[ERROR] papper pandoc defaults:" in result.stdout
    assert "pandoc-docx.yml" in result.stdout
    assert "[OK] project manuscript.md:" in result.stdout
