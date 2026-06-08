"""Shared pytest fixtures."""
import pytest

from src.nextpulse import config


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    """Rate limiting is process-global and keyed by client IP; every API test shares the
    same host ("testclient") and the same `api.app`, so the limiter would accumulate hits
    across unrelated tests and flake. Disable it by default — the dedicated rate-limit test
    re-enables it explicitly with a low threshold."""
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False, raising=False)


@pytest.fixture(autouse=True)
def _disable_intent_gate(monkeypatch):
    """The intent gate adds a classifier LLM call before every query — which would shift the
    mocked side_effect sequences / call counts the existing tests assert. Off by default; the
    dedicated intent test enables it explicitly."""
    monkeypatch.setattr(config, "INTENT_GATE", False, raising=False)
