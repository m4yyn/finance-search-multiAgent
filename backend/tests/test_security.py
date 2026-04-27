from app.core.security import create_access_token, decode_access_token, hash_password, verify_password


def test_password_hashing_uses_non_plaintext_hashes() -> None:
    hashed_password = hash_password("correct-password")

    assert hashed_password != "correct-password"
    assert verify_password("correct-password", hashed_password) is True
    assert verify_password("wrong-password", hashed_password) is False


def test_access_token_round_trip_contains_subject_and_jti() -> None:
    token = create_access_token(subject="user-id", expires_delta_minutes=30)
    payload = decode_access_token(token)

    assert payload["sub"] == "user-id"
    assert payload["jti"]
    assert payload["exp"]
    assert payload["iat"]

