"""Tests for rate_limit.py's stale-key sweep. _enforce only ever trims a key's own timestamp
list, never the key itself — a visitor/IP never seen again (the norm: visitor_id is a
client-controlled cookie that resets on every clear) would otherwise sit in the in-memory _hits
dict forever, growing it without bound over the process's lifetime. _sweep_stale_keys bounds
that growth once the dict gets large enough to matter."""

import time

import pytest

from app.core import rate_limit


@pytest.fixture(autouse=True)
def _clear_hits():
    rate_limit._hits.clear()
    yield
    rate_limit._hits.clear()


def test_sweep_removes_keys_stale_under_every_configured_window() -> None:
    cutoff = rate_limit._max_configured_window_seconds()
    now = time.monotonic()
    rate_limit._hits["stale-key"] = [now - cutoff - 10]
    rate_limit._hits["fresh-key"] = [now]
    rate_limit._hits["empty-key"] = []

    rate_limit._sweep_stale_keys()

    assert "stale-key" not in rate_limit._hits
    assert "empty-key" not in rate_limit._hits
    assert "fresh-key" in rate_limit._hits


def test_enforce_triggers_a_sweep_once_the_dict_grows_past_the_threshold(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "_SWEEP_THRESHOLD", 2)
    cutoff = rate_limit._max_configured_window_seconds()
    now = time.monotonic()
    rate_limit._hits["old-1"] = [now - cutoff - 10]
    rate_limit._hits["old-2"] = [now - cutoff - 10]
    rate_limit._hits["old-3"] = [now - cutoff - 10]  # len(_hits) == 3 > threshold of 2

    rate_limit._enforce("new-key", max_requests=10, window_seconds=60, message="x")

    assert "old-1" not in rate_limit._hits
    assert "old-2" not in rate_limit._hits
    assert "old-3" not in rate_limit._hits
    assert "new-key" in rate_limit._hits


def test_enforce_still_blocks_after_a_sweep_leaves_the_current_key_untouched(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit, "_SWEEP_THRESHOLD", 0)
    now = time.monotonic()
    rate_limit._hits["chat-ip:1.2.3.4"] = [now, now]

    with pytest.raises(Exception):
        rate_limit._enforce("chat-ip:1.2.3.4", max_requests=2, window_seconds=60, message="x")


@pytest.mark.parametrize(
    "enforce, prefix",
    [
        (rate_limit.enforce_public_chat_rate_limit, "chat"),
        (rate_limit.enforce_public_transcribe_rate_limit, "transcribe"),
        (rate_limit.enforce_chat_unlock_rate_limit, "chat-unlock"),
    ],
)
def test_visitor_rejections_do_not_use_up_the_shared_ip_budget(enforce, prefix) -> None:
    """One stuck tab past its own limit must not lock out the rest of a class behind the same IP."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.1"))
    now = time.monotonic()
    rate_limit._hits[f"{prefix}-visitor:stuck"] = [now] * 1000

    for _ in range(50):
        with pytest.raises(HTTPException):
            enforce(request, "stuck")

    assert len(rate_limit._hits.get(f"{prefix}-ip:10.0.0.1", [])) == 0
    enforce(request, "other-student")
