"""Tests for lightweight auth: token signing + login/role-from-session enforcement."""
from unittest.mock import MagicMock

import pytest

from src.nextpulse import auth


# ── token + credential logic ─────────────────────────────────────────────────

class TestTokens:
    def test_issue_and_verify_roundtrip(self):
        tok = auth.issue_token("mario", "sales")
        payload = auth.verify_token(tok)
        assert payload["u"] == "mario"
        assert payload["r"] == "sales"

    def test_tampered_token_rejected(self):
        tok = auth.issue_token("mario", "sales")
        raw, _sig = tok.rsplit(".", 1)
        assert auth.verify_token(raw + ".deadbeef") is None

    def test_expired_token_rejected(self):
        tok = auth.issue_token("mario", "sales", ttl=-1)  # already expired
        assert auth.verify_token(tok) is None

    def test_garbage_token_rejected(self):
        assert auth.verify_token("not-a-token") is None
        assert auth.verify_token(None) is None

    def test_authenticate_default_users(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_USERS", "")  # → demo defaults
        assert auth.authenticate("bid", "bid123") == "bid_manager"
        assert auth.authenticate("bid", "wrong") is None
        assert auth.authenticate("ghost", "x") is None

    def test_authenticate_custom_users(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_USERS", "anna:pw:presales")
        assert auth.authenticate("anna", "pw") == "presales"


# ── API enforcement ──────────────────────────────────────────────────────────

class _FakeVS:
    def get_stats(self):
        return {"count": 10, "collection": "x"}

    def count_sources(self):
        return 3


class _RoleEchoRAG:
    def __init__(self):
        self.vector_store = _FakeVS()
        self.model = "fake/model"
        self.pseudonymizer = MagicMock()

    def query(self, question, chat_history=None, k=None, role=None):
        return {
            "query": question, "standalone_query": question, "response": "ok",
            "sources": [], "context": [], "model": self.model, "grounded": True,
            "ambiguous": False, "obsolete": False, "top_score": 0.9,
            "role": role, "confidence": "green",  # echo the role the endpoint resolved
        }


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.nextpulse import api
    monkeypatch.setattr(api, "RAGChain", _RoleEchoRAG)
    monkeypatch.setattr("src.nextpulse.config.QUERY_LOG_ENABLED", False)
    return TestClient(api.app)


class TestAuthAPI:
    def test_me_reports_disabled(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_ENABLED", False)
        with _client(monkeypatch) as c:
            assert c.get("/api/me").json() == {"auth_enabled": False}

    def test_query_uses_client_role_when_auth_off(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_ENABLED", False)
        with _client(monkeypatch) as c:
            r = c.post("/api/query", json={"question": "ciao", "role": "sales"}).json()
        assert r["role"] == "sales"

    def test_login_sets_session_and_query_uses_server_role(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_ENABLED", True)
        monkeypatch.setattr("src.nextpulse.config.AUTH_USERS", "")  # demo users
        with _client(monkeypatch) as c:
            # No session yet → query is rejected.
            assert c.post("/api/query", json={"question": "ciao"}).status_code == 401
            # Login as the bid manager.
            login = c.post("/api/login", json={"username": "bid", "password": "bid123"})
            assert login.status_code == 200
            assert login.json()["role"] == "bid_manager"
            # Even if the client asks for 'sales', the server-verified role wins.
            r = c.post("/api/query", json={"question": "ciao", "role": "sales"}).json()
            assert r["role"] == "bid_manager"
            # /api/me reflects the session.
            me = c.get("/api/me").json()
            assert me == {"auth_enabled": True, "authenticated": True,
                          "username": "bid", "role": "bid_manager"}

    def test_bad_credentials_rejected(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_ENABLED", True)
        monkeypatch.setattr("src.nextpulse.config.AUTH_USERS", "")
        with _client(monkeypatch) as c:
            assert c.post("/api/login", json={"username": "bid", "password": "nope"}).status_code == 401

    def test_logout_clears_session(self, monkeypatch):
        monkeypatch.setattr("src.nextpulse.config.AUTH_ENABLED", True)
        monkeypatch.setattr("src.nextpulse.config.AUTH_USERS", "")
        with _client(monkeypatch) as c:
            c.post("/api/login", json={"username": "sales", "password": "sales123"})
            c.post("/api/logout")
            assert c.post("/api/query", json={"question": "ciao"}).status_code == 401
