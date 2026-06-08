"""Lightweight authentication: signed, stateless session tokens + a credential store.

Opt-in (config.AUTH_ENABLED). When on, the active role is bound to a verified session issued
at login — the client can no longer pick its own role, turning role-awareness from governance
into an actual access control (RNF6). The token is a mini-JWT (HMAC-SHA256 over a base64url
payload), so no server-side session storage is needed. Passwords live in AUTH_USERS; this is a
demo-grade store (plaintext compare via constant-time hmac) — a production deployment would use
hashed passwords and a real IdP.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Dict, Optional

from src.nextpulse import config

# Fallback demo accounts when AUTH_USERS is unset — lets the feature work out of the box once
# AUTH_ENABLED=1. Documented as demo-only; override AUTH_USERS (and AUTH_SECRET) in production.
_DEFAULT_USERS = "sales:sales123:sales,presales:presales123:presales,bid:bid123:bid_manager"


def _parse_users(spec: str) -> Dict[str, dict]:
    """Parse "user:password:role,..." into {username: {password, role}} (invalid rows skipped)."""
    out: Dict[str, dict] = {}
    for row in (spec or "").split(","):
        parts = row.split(":")
        if len(parts) == 3 and all(p.strip() for p in parts):
            user, password, role = (p.strip() for p in parts)
            out[user] = {"password": password, "role": role}
    return out


def _users() -> Dict[str, dict]:
    return _parse_users(config.AUTH_USERS or _DEFAULT_USERS)


def authenticate(username: str, password: str) -> Optional[str]:
    """Return the user's role if the credentials are valid, else None (constant-time compare)."""
    u = _users().get(username or "")
    if u and hmac.compare_digest(u["password"], password or ""):
        return u["role"]
    return None


def _sig(raw: str) -> str:
    return hmac.new(config.AUTH_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def issue_token(username: str, role: str, ttl: Optional[int] = None) -> str:
    """Mint a signed session token encoding username, role and expiry."""
    ttl = ttl if ttl is not None else config.AUTH_TOKEN_TTL_SECONDS
    payload = {"u": username, "r": role, "exp": int(time.time()) + ttl}
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()
    return f"{raw}.{_sig(raw)}"


def verify_token(token: Optional[str]) -> Optional[dict]:
    """Validate signature + expiry; return the payload ({u, r, exp}) or None."""
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sig(raw)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload
