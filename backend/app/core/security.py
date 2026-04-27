from pwdlib import PasswordHash


password_hash = PasswordHash.recommended()


def hash_secret(secret: str) -> str:
    return password_hash.hash(secret)


def verify_secret(secret: str, hashed_secret: str) -> bool:
    return password_hash.verify(secret, hashed_secret)

