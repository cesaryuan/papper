from __future__ import annotations

from pathlib import Path
import sys
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pandoc_manuscript.runtime import update_check


class FakeResponse:
    """Minimal context-managed HTTP response for update-check tests."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Leave the test response without suppressing an exception."""
        return None

    def read(self) -> bytes:
        """Return the configured JSON response body."""
        return self._body


def test_available_update_returns_newer_pypi_release(monkeypatch) -> None:
    """Notify users only when the PyPI release is newer than their install."""
    body = b'{"info": {"version": "0.5.4"}}'
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(body))

    assert update_check.available_update("0.5.3") == "0.5.4"
    assert update_check.available_update("0.5.4") is None


def test_available_update_ignores_network_failures(monkeypatch) -> None:
    """Keep completed papper commands successful when PyPI cannot be reached."""
    def fail(*args, **kwargs):
        """Simulate a failed PyPI request."""
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", fail)

    assert update_check.available_update("0.5.3") is None


def test_available_update_ignores_unexpected_pypi_json(monkeypatch) -> None:
    """Treat a structurally invalid JSON response like an unavailable update check."""
    body = b'{"info": "unexpected"}'
    monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(body))

    assert update_check.available_update("0.5.3") is None


def test_update_cache_refresh_uses_ttl_and_failure_backoff() -> None:
    """Avoid repeated worker launches while retaining a bounded retry after failure."""
    now = 100_000.0
    fresh_cache = update_check.UpdateCache("0.5.4", now - 1, now - 1)
    stale_cache = update_check.UpdateCache(
        "0.5.4",
        now - update_check.UPDATE_CACHE_TTL_SECONDS - 1,
        now - update_check.UPDATE_RETRY_SECONDS - 1,
    )
    recent_failure = update_check.UpdateCache(
        "0.5.4",
        now - update_check.UPDATE_CACHE_TTL_SECONDS - 1,
        now - 1,
    )

    assert not update_check.update_cache_needs_refresh(fresh_cache, now=now)
    assert update_check.update_cache_needs_refresh(stale_cache, now=now)
    assert not update_check.update_cache_needs_refresh(recent_failure, now=now)


def test_notify_reads_cache_then_starts_a_stale_refresh(monkeypatch, capsys) -> None:
    """Show a cached update without making the completed CLI command wait for PyPI."""
    cache = update_check.UpdateCache("0.5.4", 1.0, 1.0)
    worker_starts: list[None] = []
    monkeypatch.setattr(update_check, "read_update_cache", lambda: cache)
    monkeypatch.setattr(update_check, "update_cache_needs_refresh", lambda value: True)
    monkeypatch.setattr(update_check, "start_update_worker", lambda: worker_starts.append(None))

    update_check.notify_and_schedule_update_check("0.5.3")

    assert "Papper 0.5.4 is available" in capsys.readouterr().err
    assert worker_starts == [None]


def test_worker_retains_the_last_known_release_after_a_network_failure(monkeypatch) -> None:
    """Preserve a useful cached update while recording a failed refresh attempt."""
    previous = update_check.UpdateCache("0.5.4", 100.0, 100.0)
    written: list[update_check.UpdateCache] = []
    monkeypatch.setattr(update_check, "read_update_cache", lambda: previous)
    monkeypatch.setattr(update_check, "latest_pypi_version", lambda: None)
    monkeypatch.setattr(update_check, "write_update_cache", written.append)
    monkeypatch.setattr(update_check.time, "time", lambda: 200.0)

    update_check.run_update_worker()

    assert written == [update_check.UpdateCache("0.5.4", 100.0, 200.0)]
