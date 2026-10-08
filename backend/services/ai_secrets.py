"""Encrypt provider credentials and pending tool arguments at rest."""

import base64
import hashlib
from cryptography.fernet import Fernet
from flask import current_app


def _cipher():
    secret = current_app.config['SECRET_KEY']
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    key = base64.urlsafe_b64encode(hashlib.sha256(b'everest-optical-ai-v1:' + secret).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt(value: str) -> str:
    return _cipher().decrypt(value.encode('ascii')).decode('utf-8')
