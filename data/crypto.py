"""Field-level encryption for per-user secrets (Intervals.icu API keys, etc.)."""

from cryptography.fernet import Fernet, InvalidToken

from config import settings


def _get_fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY.get_secret_value()
    if not key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not set — cannot encrypt/decrypt user credentials")
    return Fernet(key.encode())


def encrypt_field(value: str) -> str:
    """Encrypt a plaintext value for DB storage."""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_field(encrypted: str) -> str:
    """Decrypt a value from DB. Raises ValueError on bad key or corrupted data."""
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Failed to decrypt field — wrong key or corrupted data") from e


def generate_key() -> str:
    """Generate a new Fernet key. Use once to populate FIELD_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()
