"""Пароли (PBKDF2) и JWT HS256 на стандартной библиотеке."""
import base64
import hashlib
import hmac
import json
import os
import time

from .config import settings

_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"pbkdf2${_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str, key: str) -> str:
    return _b64(hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest())


def create_token(payload: dict, ttl_minutes: int | None = None, key: str | None = None) -> str:
    key = key or settings.secret_key
    ttl = settings.token_ttl_minutes if ttl_minutes is None else ttl_minutes
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl * 60}, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}"
    return f"{signing_input}.{_sign(signing_input, key)}"


def decode_token(token: str, key: str | None = None) -> dict:
    key = key or settings.secret_key
    try:
        header, body, sig = token.split(".")
        if not hmac.compare_digest(sig, _sign(f"{header}.{body}", key)):
            raise ValueError("bad signature")
        data = json.loads(_unb64(body))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid token") from exc
    if data.get("exp", 0) < time.time():
        raise ValueError("token expired")
    return data
