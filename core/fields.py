import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models


def _get_fernet() -> Fernet:
    if settings.FERNET_KEY:
        return Fernet(settings.FERNET_KEY.encode())

    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class EncryptedTextField(models.TextField):
    description = "Encrypted text field"

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return _get_fernet().decrypt(value.encode()).decode()

    def to_python(self, value):
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        return _get_fernet().encrypt(str(value).encode()).decode()
