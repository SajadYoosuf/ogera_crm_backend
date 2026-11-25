"""
Encrypt and decrypt secrets (WhatsApp access token, app secret) before they reach the database.

Algorithm: AES-256-GCM. Stored format is  base64(nonce(12 bytes) + ciphertext + tag).
GCM authenticates the data, so a tampered value fails to decrypt instead of returning garbage.

KEY:
  WHATSAPP_ENCRYPTION_KEY — 32 random bytes, base64 encoded. Generate one with:
      python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
  Keep a backup. If the key is lost, every tenant has to enter its token again.
"""
import base64
import binascii
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class CryptoError(Exception):
    """The key is missing or invalid, or a stored value cannot be decrypted."""


def _aesgcm() -> AESGCM:
    raw = os.getenv("WHATSAPP_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise CryptoError("WHATSAPP_ENCRYPTION_KEY is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise CryptoError("WHATSAPP_ENCRYPTION_KEY is not valid base64")
    if len(key) != 32:
        raise CryptoError("WHATSAPP_ENCRYPTION_KEY must decode to 32 bytes")
    return AESGCM(key)


def encrypt_secret(plain: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    cipher = _aesgcm().encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + cipher).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        blob = base64.b64decode(token, validate=True)
        nonce, cipher = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        return _aesgcm().decrypt(nonce, cipher, None).decode("utf-8")
    except (binascii.Error, ValueError, InvalidTag):
        raise CryptoError("Stored secret cannot be decrypted (wrong key or damaged value)")


def mask_secret(plain: str) -> str:
    """Short hint for the UI, e.g. 'EAAG…x7Qz'. Short values are fully hidden."""
    if len(plain) <= 12:
        return "••••••••"
    return f"{plain[:4]}…{plain[-4:]}"
