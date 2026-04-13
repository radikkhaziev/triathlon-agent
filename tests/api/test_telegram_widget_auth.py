"""Tests for Telegram Login Widget signature verification.

Per https://core.telegram.org/widgets/login#checking-authorization
"""

import hashlib
import hmac
import time

import pytest
from pydantic import SecretStr

from api.auth import TELEGRAM_AUTH_MAX_AGE_SECONDS, verify_telegram_widget_auth
from config import settings

BOT_TOKEN = "123456:TEST-BOT-TOKEN-FOR-UNIT-TESTS"


def _sign(data: dict, bot_token: str = BOT_TOKEN) -> dict:
    """Produce a signed payload the same way Telegram does, for test inputs."""
    fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    sig = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return {**data, "hash": sig}


@pytest.fixture(autouse=True)
def _stub_bot_token(monkeypatch):
    """Every test runs with a deterministic bot token."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", SecretStr(BOT_TOKEN))


def _fresh_payload(**overrides) -> dict:
    base = {
        "id": 42,
        "first_name": "Radik",
        "username": "radik",
        "photo_url": "https://t.me/i/userpic/320/radik.jpg",
        "auth_date": int(time.time()),
    }
    base.update(overrides)
    return _sign(base)


def test_valid_signature_returns_chat_id():
    payload = _fresh_payload()
    assert verify_telegram_widget_auth(payload) == "42"


def test_valid_signature_without_optional_fields():
    # Only `id` and `auth_date` are strictly required from Telegram.
    payload = _sign({"id": 7, "auth_date": int(time.time())})
    assert verify_telegram_widget_auth(payload) == "7"


def test_invalid_signature_rejected():
    payload = _fresh_payload()
    payload["hash"] = "0" * 64
    assert verify_telegram_widget_auth(payload) is None


def test_tampered_field_rejected():
    payload = _fresh_payload()
    payload["id"] = 999  # id changed after signing → hash mismatch
    assert verify_telegram_widget_auth(payload) is None


def test_missing_hash_rejected():
    payload = _fresh_payload()
    del payload["hash"]
    assert verify_telegram_widget_auth(payload) is None


def test_missing_id_rejected():
    base = {"first_name": "X", "auth_date": int(time.time())}
    assert verify_telegram_widget_auth(_sign(base)) is None


def test_missing_auth_date_rejected():
    base = {"id": 1, "first_name": "X"}
    assert verify_telegram_widget_auth(_sign(base)) is None


def test_stale_auth_date_rejected():
    stale_ts = int(time.time()) - TELEGRAM_AUTH_MAX_AGE_SECONDS - 1
    payload = _fresh_payload(auth_date=stale_ts)
    assert verify_telegram_widget_auth(payload) is None


def test_future_auth_date_rejected():
    future_ts = int(time.time()) + 3600
    payload = _fresh_payload(auth_date=future_ts)
    assert verify_telegram_widget_auth(payload) is None


def test_wrong_bot_token_rejected():
    # Signed with another token — must not pass verification against our token.
    payload = _sign(
        {
            "id": 1,
            "first_name": "X",
            "auth_date": int(time.time()),
        },
        bot_token="another:TOKEN",
    )
    assert verify_telegram_widget_auth(payload) is None


def test_missing_bot_token_rejected(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", SecretStr(""))
    payload = _fresh_payload()
    assert verify_telegram_widget_auth(payload) is None


def test_non_string_hash_rejected():
    payload = _fresh_payload()
    payload["hash"] = 12345
    assert verify_telegram_widget_auth(payload) is None


def test_integer_id_returned_as_string():
    payload = _fresh_payload(id=777)
    assert verify_telegram_widget_auth(payload) == "777"


def test_null_optional_field_is_accepted():
    """A stray JSON null in an optional field must not break verification.

    Telegram itself never emits null, but clients that serialize undefined as
    null should still succeed — we strip None values before signing.
    """
    signed = _sign(
        {
            "id": 42,
            "first_name": "Radik",
            "auth_date": int(time.time()),
        }
    )
    # Client adds last_name=null after Telegram already signed it:
    signed["last_name"] = None
    assert verify_telegram_widget_auth(signed) == "42"


def test_extra_unknown_fields_are_signed_through():
    """Any extra field Telegram adds in the future must be included in HMAC."""
    payload = _sign(
        {
            "id": 1,
            "first_name": "X",
            "auth_date": int(time.time()),
            "new_field_from_telegram": "surprise",
        }
    )
    assert verify_telegram_widget_auth(payload) == "1"


def test_extra_field_tampered_rejected():
    """If an extra field is added after signing, HMAC must fail."""
    payload = _fresh_payload()
    payload["injected"] = "malicious"
    assert verify_telegram_widget_auth(payload) is None


def test_empty_payload_rejected():
    assert verify_telegram_widget_auth({}) is None


def test_string_id_returned_verbatim():
    """Telegram's HTTP-redirect flow encodes id as a query-string (string)."""
    payload = _sign({"id": "12345", "auth_date": int(time.time())})
    assert verify_telegram_widget_auth(payload) == "12345"
