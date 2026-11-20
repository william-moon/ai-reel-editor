"""Encrypted store for site logins the agent uses on the person's behalf (today: the Envato email + password).

How it is protected:
- The values are encrypted with AES-256-GCM (authenticated: a changed or damaged file is refused, never half-read)
  and written to vault.bin in a private folder OUTSIDE the repo (SECRETS_DIR, default ~/.ai-reel-editor, mode 700,
  files mode 600). Nothing is ever written to .env, git, logs or chat.
- The 256-bit master key lives in the operating system's own password store when there is one: macOS Keychain,
  Windows Credential Manager, or the Linux Secret Service (GNOME Keyring / KWallet). So a copy of the folder alone
  (a backup, a synced folder, a stolen disk image) cannot be decrypted.
- On a headless Linux server with no password store, the key goes in vault.key (mode 600) next to the vault. That
  still keeps the login out of the repo, backups of the repo and every log, but anyone who is already logged in as
  that user (or root) on the server could read both files. `vault status` says which mode is in use.
- Values are handed to the browser helper over a pipe (stdin), never on the command line or in environment
  variables, so other programs cannot see them in the process list.

  python -m reel_editor vault set ENVATO_EMAIL -      read the value from stdin (or a hidden prompt on a terminal)
  python -m reel_editor vault status                  which logins are saved (never the values) + protection mode
  python -m reel_editor vault delete ENVATO_PASSWORD
"""
import base64
import json
import os
import secrets
import stat
import sys

from . import config as C

ALLOWED = ("ENVATO_EMAIL", "ENVATO_PASSWORD")
SERVICE = "ai-reel-editor"
KEY_NAME = "vault-master-key"


def _dir():
    d = os.path.expanduser(C.env("SECRETS_DIR", "~/.ai-reel-editor"))
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _vault_path():
    return os.path.join(_dir(), "vault.bin")


def _keyfile_path():
    return os.path.join(_dir(), "vault.key")


def _write_private(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _keyring():
    """The OS password store, or None (headless Linux, or keyring not installed / only a fake backend)."""
    if C.env("VAULT_NO_KEYCHAIN") == "1":
        return None
    try:
        import keyring
        kr = keyring.get_keyring()
        name = f"{type(kr).__module__}.{type(kr).__name__}".lower()
        if any(b in name for b in ("fail", "null", "plaintext")):
            return None
        if "chainer" in name and not getattr(kr, "backends", None):
            return None
        return keyring
    except Exception:
        return None


def mode():
    return "os-keychain" if _keyring() else "key-file"


def _master_key(create=True):
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(SERVICE, KEY_NAME)
            if v:
                return base64.b64decode(v)
            if create:
                k = secrets.token_bytes(32)
                kr.set_password(SERVICE, KEY_NAME, base64.b64encode(k).decode())
                return k
            return None
        except Exception:
            pass                        # locked / unavailable keychain: fall back to the key file
    p = _keyfile_path()
    if os.path.exists(p):
        with open(p, "rb") as fh:
            return base64.b64decode(fh.read().strip())
    if not create:
        return None
    k = secrets.token_bytes(32)
    _write_private(p, base64.b64encode(k))
    return k


def _load():
    p = _vault_path()
    if not os.path.exists(p):
        return {}
    key = _master_key(create=False)
    if not key:
        raise RuntimeError("vault key missing (was the keychain entry removed?): save the logins again")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(p, "rb") as fh:
        blob = fh.read()
    if blob[:4] != b"ARV1":
        raise RuntimeError("not a vault file")
    data = AESGCM(key).decrypt(blob[4:16], blob[16:], b"ai-reel-editor-vault")   # raises if tampered
    return json.loads(data.decode())


def _save(d):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _master_key(create=True)
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(d).encode(), b"ai-reel-editor-vault")
    _write_private(_vault_path(), b"ARV1" + nonce + ct)


def put(name, value):
    if name not in ALLOWED:
        raise ValueError(f"{name} is not a vault entry (allowed: {', '.join(ALLOWED)})")
    value = (value or "").strip("\r\n")
    if not value:
        raise ValueError("empty value")
    d = _load()
    d[name] = value
    _save(d)


def get(name):
    migrate_from_env()
    try:
        return _load().get(name)
    except Exception:
        return None


def delete(name):
    d = _load()
    if d.pop(name, None) is not None:
        _save(d)
        return True
    return False


def has_login(site="envato"):
    return bool(get("ENVATO_EMAIL") and get("ENVATO_PASSWORD")) if site == "envato" else False


def migrate_from_env():
    """Older setups kept ENVATO_EMAIL / ENVATO_PASSWORD in .env as plain text: move them into the vault, blank them."""
    moved = []
    for k in ALLOWED:
        v = os.environ.get(k, "")
        if v:
            try:
                d = _load()
                d[k] = v
                _save(d)
            except Exception:
                continue
            moved.append(k)
            os.environ.pop(k, None)
            if os.path.exists(C.ENV_FILE):
                C.set_env_value(k, "")
                os.environ.pop(k, None)
    return moved


def status():
    try:
        d = _load()
        ok = True
    except Exception as ex:
        d, ok = {}, str(ex)
    return {"protection": mode(), "folder": _dir(), "readable": ok,
            "saved": {k: k in d for k in ALLOWED}}


def _read_value():
    if sys.stdin.isatty():
        import getpass
        return getpass.getpass("value (hidden): ")
    return sys.stdin.readline()


def main(args):
    sub = args[0] if args else "status"
    if sub == "set" and len(args) >= 2:
        name = args[1]
        val = _read_value() if len(args) < 3 or args[2] == "-" else args[2]
        put(name, val)
        print(f"saved {name} (encrypted, {mode()})")
        return 0
    if sub == "delete" and len(args) >= 2:
        print("deleted" if delete(args[1]) else "not saved")
        return 0
    if sub == "status":
        print(json.dumps(status(), indent=1))
        return 0
    print(__doc__)
    return 2
