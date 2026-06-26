import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models


def _legacy_secret_key_fernet() -> Fernet:
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _configured_fernet_keys() -> list[str]:
    configured = [str(value or "").strip() for value in getattr(settings, "FERNET_KEYS", []) if str(value or "").strip()]
    primary = str(getattr(settings, "FERNET_KEY", "") or "").strip()
    if primary and primary not in configured:
        configured.insert(0, primary)
    if configured:
        return configured
    return [base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()]


def _get_fernet() -> MultiFernet:
    return MultiFernet([Fernet(key.encode()) for key in _configured_fernet_keys()])


class TokenDecryptionError(Exception):
    """Raised when a value that is clearly Fernet ciphertext cannot be decrypted
    with any configured key (key loss/rotation gap). We must NOT hand the raw
    ciphertext back as if it were the secret — that ships a garbage token to Meta
    and leaks ciphertext into logs."""


# Fernet tokens are urlsafe-base64 of a payload whose first (version) byte is 0x80,
# which always base64-encodes to the prefix "gAAAAA". Real Meta tokens start with
# "EAA", so this prefix reliably distinguishes "encrypted but undecryptable" from
# "legacy plaintext".
_FERNET_TOKEN_PREFIX = "gAAAAA"


def _decrypt_if_encrypted(value, *, allow_plaintext: bool = True):
    if value in (None, ""):
        return value

    normalized = str(value)
    for fernet in (_get_fernet(), _legacy_secret_key_fernet()):
        try:
            return fernet.decrypt(normalized.encode()).decode()
        except InvalidToken:
            continue

    # Neither the configured keys nor the legacy key could decrypt it.
    if normalized.startswith(_FERNET_TOKEN_PREFIX):
        raise TokenDecryptionError(
            "Stored encrypted value could not be decrypted with any configured Fernet key."
        )
    if not allow_plaintext:
        raise TokenDecryptionError("Value is not decryptable and plaintext is not allowed here.")
    # Legacy unencrypted value from before encryption was introduced — return as-is.
    return normalized


def encrypt_text(value):
    """Encrypt a string with the configured Fernet keys (for at-rest cache/session use)."""
    if value in (None, ""):
        return value
    return _get_fernet().encrypt(str(value).encode()).decode()


def decrypt_text(value):
    """Decrypt a value produced by encrypt_text; returns plaintext as-is if not encrypted.

    The plaintext fallback keeps any pre-existing unencrypted cache entries working
    through the transition window.
    """
    return _decrypt_if_encrypted(value)


class EncryptedTextField(models.TextField):
    description = "Encrypted text field"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return _decrypt_if_encrypted(value)

    def to_python(self, value):
        return _decrypt_if_encrypted(value)

    def get_prep_value(self, value):
        if value is None:
            return value
        normalized = _decrypt_if_encrypted(value)
        return _get_fernet().encrypt(str(normalized).encode()).decode()
