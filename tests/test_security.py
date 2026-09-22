import pytest

from farelens.core.security import (
    SecretBox,
    create_session_token,
    decode_session_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    load_or_create_secret,
    mask_secret,
    verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-hash")


def test_secret_box_roundtrip_and_key_change():
    box = SecretBox("secret-a")
    token = box.encrypt("sk-live-123")
    assert token.startswith("enc:v1:")
    assert box.decrypt(token) == "sk-live-123"
    assert box.decrypt("plain") == "plain"  # tolerated
    with pytest.raises(ValueError):
        SecretBox("secret-b").decrypt(token)


def test_api_key_generation():
    key, prefix, digest = generate_api_key()
    assert key.startswith("fl_") and key.startswith(prefix)
    assert digest == hash_api_key(key)
    assert mask_secret(key).endswith(key[-4:])


def test_session_token_roundtrip():
    tok = create_session_token("s3cret", user_id=7, username="admin", hours=1)
    payload = decode_session_token("s3cret", tok)
    assert payload and payload["sub"] == "7" and payload["username"] == "admin"
    assert decode_session_token("other", tok) is None


def test_load_or_create_secret_persists(tmp_path):
    first = load_or_create_secret("", tmp_path)
    second = load_or_create_secret("", tmp_path)
    assert first == second and len(first) > 32
    assert load_or_create_secret("explicit", tmp_path) == "explicit"
