"""Decrypt WhatsApp `.crypt15` backups with the user's 64-digit key.

Format (reverse engineered by the wa-crypt-tools project, GPL-3.0, and re-implemented
here from the published description so WaBridge has no hard dependency on it):

    ┌──────────────┬──────────────────────────┬──────────────────────┬──────────┐
    │ varint: hlen │ protobuf header (hlen B) │ AES-256-GCM payload  │ GCM tag  │
    └──────────────┴──────────────────────────┴──────────────────────┴──────────┘

* The header is a protobuf `BackupPrefix`; somewhere inside it (nested message
  `c15_iv`, field `IV`) is the 16-byte GCM nonce. We don't hard-code field numbers;
  we walk the protobuf generically and take the 16-byte `bytes` field we find.
* Key derivation from the 32-byte root key R (what the 64 hex digits encode):
      PRK     = HMAC-SHA256(key = 32 zero bytes, msg = R)
      AES_KEY = HMAC-SHA256(key = PRK, msg = b"backup encryption" + 0x01)
  which is exactly HKDF-SHA256(salt=0*32, ikm=R, info=b"backup encryption", L=32).
* The decrypted payload is zlib-compressed SQLite (starts 0x78) unless WhatsApp
  chose not to compress, in which case it starts with b"SQLite format 3\\x00".

Some newer WhatsApp builds append extra trailer bytes. `decrypt()` therefore tries the
canonical layout first and then a couple of tolerant fallbacks, always validating
that the result is a real SQLite database before declaring success.
"""

from __future__ import annotations

import hmac
import os
import re
import zlib
from dataclasses import dataclass
from hashlib import sha256

SQLITE_MAGIC = b"SQLite format 3\x00"
BACKUP_INFO = b"backup encryption"
GCM_TAG_LEN = 16
IV_LEN = 16


class Crypt15Error(Exception):
    pass


# ----------------------------------------------------------------------------- key handling

def parse_key(key: str | bytes | os.PathLike) -> bytes:
    """Accept the 64-digit key (with or without spaces), a hex string, a 32-byte raw key,
    or a path to `encrypted_backup.key`. Return the 32-byte root key."""
    if isinstance(key, (bytes, bytearray)):
        data = bytes(key)
        if len(data) == 32:
            return data
        return _key_from_keyfile_bytes(data)

    s = str(key).strip()
    if os.path.isfile(s):
        with open(s, "rb") as fh:
            data = fh.read()
        # a text file holding the 64 hex digits (key.txt) vs the binary encrypted_backup.key
        as_text = re.sub(r"[\s\-:]", "", data.decode("ascii", errors="ignore"))
        if re.fullmatch(r"[0-9a-fA-F]{64}", as_text):
            return bytes.fromhex(as_text)
        return _key_from_keyfile_bytes(data)
    hexstr = re.sub(r"[\s\-:]", "", s)
    if re.fullmatch(r"[0-9a-fA-F]{64}", hexstr):
        return bytes.fromhex(hexstr)
    raise Crypt15Error(
        "Key must be the 64-digit key shown by WhatsApp (Settings > Chats > Chat backup > "
        "End-to-end encrypted backup), or a path to encrypted_backup.key."
    )


def _key_from_keyfile_bytes(data: bytes) -> bytes:
    """`encrypted_backup.key` (root-only location) is a small Java-serialised blob whose
    last 32 bytes are the root key. Best-effort: we only need those bytes."""
    if len(data) < 32:
        raise Crypt15Error("Key file too short")
    return data[-32:]


def derive_aes_key(root_key: bytes) -> bytes:
    if len(root_key) != 32:
        raise Crypt15Error("Root key must be 32 bytes")
    prk = hmac.new(b"\x00" * 32, root_key, sha256).digest()
    return hmac.new(prk, BACKUP_INFO + b"\x01", sha256).digest()


# ----------------------------------------------------------------------------- protobuf walk

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(buf):
            raise Crypt15Error("Truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise Crypt15Error("Varint too long")


def _walk_protobuf(buf: bytes, depth: int = 0, max_depth: int = 6) -> list[bytes]:
    """Return every length-delimited field value found at any nesting depth.
    Invalid sub-messages are simply not descended into."""
    found: list[bytes] = []
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        wire = tag & 7
        if wire == 0:                      # varint
            _, pos = _read_varint(buf, pos)
        elif wire == 1:                    # 64-bit
            pos += 8
        elif wire == 2:                    # length-delimited
            length, pos = _read_varint(buf, pos)
            val = buf[pos:pos + length]
            if len(val) != length:
                raise Crypt15Error("Truncated length-delimited field")
            pos += length
            found.append(val)
            if depth < max_depth and val:
                try:
                    found.extend(_walk_protobuf(val, depth + 1, max_depth))
                except Crypt15Error:
                    pass
        elif wire == 5:                    # 32-bit
            pos += 4
        else:
            raise Crypt15Error(f"Unsupported wire type {wire}")
        if pos > len(buf):
            raise Crypt15Error("Truncated field")
    return found


@dataclass
class Crypt15Header:
    length: int           # header bytes (excluding the varint prefix)
    prefix_len: int       # size of the varint prefix
    iv_candidates: list[bytes]   # every 16-byte field in the prefix; the IV is one of them
    raw: bytes

    @property
    def iv(self) -> bytes:
        return self.iv_candidates[0]

    @property
    def payload_offset(self) -> int:
        return self.prefix_len + self.length


def parse_header(data: bytes) -> Crypt15Header:
    hlen, prefix_len = _read_varint(data, 0)
    if hlen <= 0 or hlen > 4096 or prefix_len + hlen > len(data):
        raise Crypt15Error(f"Implausible header length {hlen}; is this really a .crypt15 file?")
    raw = data[prefix_len:prefix_len + hlen]
    candidates = [v for v in _walk_protobuf(raw) if len(v) == IV_LEN]
    if not candidates:
        raise Crypt15Error("Could not locate the 16-byte IV inside the crypt15 header")
    # A 16-char string field (e.g. an app version) could also be 16 bytes long, so we keep
    # every candidate, innermost-looking first, and let GCM authentication pick the right one.
    ordered = sorted(dict.fromkeys(candidates), key=lambda v: all(0x20 <= b < 0x7F for b in v))
    return Crypt15Header(length=hlen, prefix_len=prefix_len, iv_candidates=ordered, raw=raw)


# ----------------------------------------------------------------------------- decrypt

def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:   # pragma: no cover
        raise Crypt15Error("Install the 'cryptography' package: pip install cryptography") from e
    return AESGCM


def _finish(plain: bytes) -> bytes:
    if plain[:2] == b"\x78\x9c" or plain[:1] == b"\x78":
        try:
            plain = zlib.decompress(plain)
        except zlib.error as e:
            raise Crypt15Error(f"zlib decompression failed: {e}") from e
    if not plain.startswith(SQLITE_MAGIC):
        raise Crypt15Error("Decrypted data is not a SQLite database (wrong key?)")
    return plain


def decrypt_bytes(data: bytes, key: str | bytes, *, force: bool = False) -> bytes:
    """Decrypt a whole crypt15 file held in memory. Returns the raw SQLite bytes."""
    root = parse_key(key)
    aes_key = derive_aes_key(root)
    header = parse_header(data)
    body = data[header.payload_offset:]
    if len(body) <= GCM_TAG_LEN:
        raise Crypt15Error("File too small")

    AESGCM = _aesgcm()
    gcm = AESGCM(aes_key)
    errors: list[str] = []

    # Canonical layout: ciphertext || tag(16)
    # Tolerant layouts: some builds append a 16-byte MD5 trailer after the tag.
    for iv in header.iv_candidates:
        for trailer in (0, 16):
            chunk = body[: len(body) - trailer] if trailer else body
            try:
                plain = gcm.decrypt(iv, chunk, None)
                return _finish(plain)
            except Exception as e:  # InvalidTag or our own checks
                errors.append(f"iv={iv.hex()[:8]}… trailer={trailer}: {type(e).__name__}: {e}")

    if force:
        # Authenticate nothing; just run the GCM keystream and check the result looks like a
        # database. (For 128-bit IVs J0 = GHASH(IV), so we need the real GCM decryptor.)
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        ct = body[:-GCM_TAG_LEN]
        for iv in header.iv_candidates:
            dec = Cipher(algorithms.AES(aes_key), modes.GCM(iv, b"\x00" * 16, min_tag_length=16)).decryptor()
            try:
                return _finish(dec.update(ct))
            except Crypt15Error as e:
                errors.append(f"force iv={iv.hex()[:8]}…: {e}")

    raise Crypt15Error(
        "Could not decrypt. Check the 64-digit key belongs to THIS backup "
        "(re-run Chat backup > Back up after enabling end-to-end encrypted backup).\n  "
        + "\n  ".join(errors)
    )


def decrypt_file(src: str | os.PathLike, dst: str | os.PathLike, key: str | bytes, *, force: bool = False) -> str:
    with open(src, "rb") as fh:
        data = fh.read()
    plain = decrypt_bytes(data, key, force=force)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = f"{dst}.part"
    with open(tmp, "wb") as fh:
        fh.write(plain)
    os.replace(tmp, dst)
    return str(dst)


# ----------------------------------------------------------------------------- encrypt (tests)

def encrypt_bytes(sqlite_bytes: bytes, key: str | bytes, *, iv: bytes | None = None, compress: bool = True) -> bytes:
    """Produce a crypt15-shaped file. Used by the test-suite for round-trips and handy for
    building fixtures; NOT guaranteed to be byte-identical to WhatsApp's own encoder
    (field numbers in the prefix are approximated)."""
    root = parse_key(key)
    aes_key = derive_aes_key(root)
    iv = iv or os.urandom(IV_LEN)
    # BackupPrefix { key_type=1 (varint) ; c15_iv (field 3, message) { IV (field 1, bytes) } }
    inner = bytes([0x0A, IV_LEN]) + iv
    header = bytes([0x08, 0x01, 0x1A, len(inner)]) + inner
    payload = zlib.compress(sqlite_bytes) if compress else sqlite_bytes
    ct = _aesgcm()(aes_key).encrypt(iv, payload, None)
    return bytes([len(header)]) + header + ct


def wadecrypt_fallback(src: str, dst: str, key: str) -> bool:
    """If `wa-crypt-tools` is installed, shell out to it. Returns True on success."""
    import shutil
    import subprocess

    exe = shutil.which("wadecrypt")
    if not exe:
        return False
    r = subprocess.run([exe, parse_key(key).hex(), src, dst], capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(dst)
