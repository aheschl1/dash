"""Password hashing and stateless signed tokens — stdlib only (no extra deps).

Passwords use PBKDF2-HMAC-SHA256 with a per-user salt. Session tokens are a
compact `<payload>.<signature>` pair signed with HMAC-SHA256 against a server
secret persisted in Postgres, so they survive container rebuilds within their TTL.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

PBKDF2_ITERATIONS = 200_000
TOKEN_TTL_SECONDS = 24 * 3600


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return salt, dk.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    _, actual = hash_password(password, salt)
    return hmac.compare_digest(actual, expected_hash)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64url_encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def create_token(username: str, role: str, secret: str, ttl: int = TOKEN_TTL_SECONDS) -> tuple[str, int]:
    exp = int(time.time()) + ttl
    payload = {"sub": username, "role": role, "exp": exp}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body, secret)}", exp


def verify_token(token: str, secret: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body, secret)):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload
