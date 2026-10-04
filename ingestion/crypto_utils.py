"""
Passphrase-based encryption helpers shared by:

  - scripts/encrypt_bank_csv.py   (encrypt your real export once, locally)
  - ingestion/load_to_duckdb.py   (decrypt it in memory at load time)

Design, and why it's shaped this way:

  - Fernet (AES-128-CBC + HMAC-SHA256, via the `cryptography` package) for
    the actual encryption -- authenticated, so a tampered or corrupted
    file fails to decrypt instead of silently returning garbage.
  - The key itself is never stored anywhere. It's derived from a
    passphrase you type, via PBKDF2-HMAC-SHA256 with a random per-file
    salt. The salt isn't secret (it's stored alongside the ciphertext,
    as the first 16 bytes of the .enc file) -- its job is only to make
    two people using the same passphrase produce different ciphertext,
    and to make precomputed/rainbow-table attacks impractical.
  - Consequence worth being explicit about: if you forget the passphrase,
    the data is gone. There is no recovery path, by design -- a recovery
    mechanism would be a second way in, which defeats the point.

This module is intentionally dependency-light (just `cryptography` +
stdlib) so it can be imported from a plain script with no dbt/duckdb/
streamlit context, and also from the dashboard process.
"""

from __future__ import annotations

import base64
import getpass
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 480_000  # OWASP's 2023 minimum recommendation for PBKDF2-SHA256
SALT_SIZE = 16

__all__ = [
    "InvalidToken",
    "derive_key",
    "encrypt_bytes",
    "decrypt_bytes",
    "prompt_passphrase",
]


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256(passphrase, salt) -> a urlsafe-base64 Fernet key."""
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    """Returns salt(16 bytes) + Fernet token. The salt travels with the
    ciphertext on purpose -- see module docstring."""
    salt = os.urandom(SALT_SIZE)
    key = derive_key(passphrase, salt)
    token = Fernet(key).encrypt(data)
    return salt + token


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    """Inverse of encrypt_bytes(). Raises cryptography.fernet.InvalidToken
    if the passphrase is wrong or the ciphertext was tampered with/corrupted
    -- callers should catch that and fail loudly rather than guess."""
    if len(blob) < SALT_SIZE:
        raise InvalidToken("Encrypted file is too short to contain a valid salt + token.")
    salt, token = blob[:SALT_SIZE], blob[SALT_SIZE:]
    key = derive_key(passphrase, salt)
    return Fernet(key).decrypt(token)


def prompt_passphrase(confirm: bool = False, env_var: str = "BANK_DATA_PASSPHRASE") -> str:
    """Reads the passphrase from an env var if set (handy for CI / non-interactive
    runs), otherwise prompts via getpass (never echoed, never logged)."""
    env_val = os.environ.get(env_var)
    if env_val:
        return env_val
    pw = getpass.getpass("Passphrase for your encrypted bank data: ")
    if not pw:
        raise SystemExit("A non-empty passphrase is required.")
    if confirm:
        pw2 = getpass.getpass("Confirm passphrase: ")
        if pw != pw2:
            raise SystemExit("Passphrases did not match -- nothing was written.")
    return pw
