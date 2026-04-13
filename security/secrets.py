"""
Helix Secrets Manager
Fernet-encrypted credential store at ~/.helix/secrets.enc
Master key derived via argon2id from machine entropy + optional passphrase.
"""

import os
import json
import hashlib
import base64
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet
from argon2.low_level import hash_secret_raw, Type


SECRETS_PATH = Path.home() / ".helix" / "secrets.enc"
SALT_PATH = Path.home() / ".helix" / ".salt"


def _machine_entropy() -> bytes:
    """Stable per-machine entropy from machine-id."""
    sources = [
        Path("/etc/machine-id"),
        Path("/var/lib/dbus/machine-id"),
    ]
    for src in sources:
        if src.exists():
            return src.read_bytes().strip()
    # Fallback: hash of hostname + uid
    import socket
    return hashlib.sha256(f"{socket.gethostname()}:{os.getuid()}".encode()).digest()


def _get_or_create_salt() -> bytes:
    SALT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SALT_PATH.exists():
        return base64.b64decode(SALT_PATH.read_bytes())
    salt = os.urandom(16)
    SALT_PATH.write_bytes(base64.b64encode(salt))
    SALT_PATH.chmod(0o600)
    return salt


def _derive_key(passphrase: str = "") -> bytes:
    """Derive 32-byte key via argon2id from machine entropy + passphrase."""
    salt = _get_or_create_salt()
    password = _machine_entropy() + passphrase.encode()
    raw = hash_secret_raw(
        secret=password,
        salt=salt,
        time_cost=2,
        memory_cost=65536,
        parallelism=2,
        hash_len=32,
        type=Type.ID,
    )
    return base64.urlsafe_b64encode(raw)


def _get_fernet(passphrase: str = "") -> Fernet:
    return Fernet(_derive_key(passphrase))


def _load_secrets(passphrase: str = "") -> dict:
    if not SECRETS_PATH.exists():
        return {}
    fernet = _get_fernet(passphrase)
    encrypted = SECRETS_PATH.read_bytes()
    decrypted = fernet.decrypt(encrypted)
    return json.loads(decrypted)


def _save_secrets(data: dict, passphrase: str = "") -> None:
    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fernet = _get_fernet(passphrase)
    encrypted = fernet.encrypt(json.dumps(data).encode())
    SECRETS_PATH.write_bytes(encrypted)
    SECRETS_PATH.chmod(0o600)


def set_secret(key: str, value: str, passphrase: str = "") -> None:
    """Store a secret by key."""
    data = _load_secrets(passphrase)
    data[key] = value
    _save_secrets(data, passphrase)


def get_secret(key: str, passphrase: str = "", default: Optional[str] = None) -> Optional[str]:
    """Retrieve a secret by key."""
    data = _load_secrets(passphrase)
    return data.get(key, default)


def delete_secret(key: str, passphrase: str = "") -> bool:
    """Delete a secret. Returns True if it existed."""
    data = _load_secrets(passphrase)
    if key in data:
        del data[key]
        _save_secrets(data, passphrase)
        return True
    return False


def list_keys(passphrase: str = "") -> list[str]:
    """List all secret keys (not values)."""
    return list(_load_secrets(passphrase).keys())
