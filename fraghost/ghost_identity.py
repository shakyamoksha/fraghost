"""Local WireGuard identity. The private key is DPAPI-wrapped and is never sent anywhere."""

from __future__ import annotations

import base64

from .secrets import read_secret, write_secret

_NAME = "ghost_private"
_public = ""


class IdentityError(Exception):
    pass


def public_key(*, create: bool) -> str:
    global _public
    if _public:
        return _public
    raw = read_secret(_NAME)
    if len(raw) != 32 and create:
        raw = _generate()
        if raw is None or not write_secret(_NAME, raw):
            return ""
    if len(raw) != 32:
        return ""
    try:
        _public = encode_key(_public_bytes(raw))
    except IdentityError:
        return ""
    return _public


def private_key() -> bytes:
    raw = read_secret(_NAME)
    if len(raw) != 32:
        raw = _generate()
        if raw is None or not write_secret(_NAME, raw):
            raise IdentityError("The local WireGuard key could not be saved. Transfers stay paused.")
    return raw


def encode_key(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def decode_key(text: str) -> bytes:
    compact = "".join(str(text or "").split())
    if not compact:
        raise ValueError("A WireGuard public key is required.")
    padded = compact + ("=" * (-len(compact) % 4))
    try:
        decoded = base64.b64decode(padded, validate=True)
    except Exception as exc:
        raise ValueError("The WireGuard key is not valid.") from exc
    if len(decoded) != 32:
        raise ValueError("The WireGuard key is not valid.")
    return decoded


def _generate() -> bytes | None:
    try:
        private = _x25519()[0].generate()
    except IdentityError:
        return None
    return _raw_private(private)


def _public_bytes(raw: bytes) -> bytes:
    private_cls, _, serialization = _x25519()
    private = private_cls.from_private_bytes(raw)
    return private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(private) -> bytes:
    _, _, serialization = _x25519()
    return private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def _x25519():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    except ImportError as exc:
        raise IdentityError("WireGuard support is not installed. Transfers stay paused.") from exc
    return X25519PrivateKey, None, serialization
