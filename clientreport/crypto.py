"""Encrypt secrets at rest with the app's Fernet key."""

from cryptography.fernet import Fernet


def encrypt(key: str, text: str) -> str:
    return Fernet(key.encode()).encrypt(text.encode()).decode()


def decrypt(key: str, token: str) -> str:
    return Fernet(key.encode()).decrypt(token.encode()).decode()
