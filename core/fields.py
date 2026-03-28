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


def _decrypt_if_encrypted(value):
    if value in (None, ""):
        return value

    normalized = str(value)
    try:
        return _get_fernet().decrypt(normalized.encode()).decode()
    except InvalidToken:
        try:
            return _legacy_secret_key_fernet().decrypt(normalized.encode()).decode()
        except InvalidToken:
            return normalized


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
