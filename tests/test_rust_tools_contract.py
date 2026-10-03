"""Exercise native tool diagnostics and detached update refresh at real process boundaries."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

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
