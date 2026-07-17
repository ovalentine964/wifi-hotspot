"""
Encryption layer for Nokia G-2425G-A router communication.

The router expects encrypted POST payloads:
  1. Generate random 16-byte AES key + 16-byte IV
  2. Encrypt payload with AES-128-CBC (PKCS7 padding)
  3. Encrypt AES key+IV concatenated with RSA-1024 (PKCS1v15) using router's pubkey
  4. Send: encrypted=1&ct=<base64url(ciphertext)>&ck=<base64url(encrypted_key)>
"""

import base64
import os
import logging

import pyaes
import rsa

logger = logging.getLogger(__name__)


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def encrypt_payload(plaintext: str, modulus_hex: str, exponent_hex: str) -> dict:
    """
    Encrypt a plaintext form-data string for the router.

    Args:
        plaintext: URL-encoded form string, e.g. "username=admin&password=secret"
        modulus_hex: RSA modulus as hex string (from router page JS)
        exponent_hex: RSA exponent as hex string (from router page JS)

    Returns:
        dict with keys: encrypted, ct, ck  (ready for POST)
    """
    # 1. Random AES key and IV
    aes_key = os.urandom(16)
    iv = os.urandom(16)

    # 2. AES-128-CBC encrypt
    padded = pkcs7_pad(plaintext.encode("utf-8"))
    # pyaes CBC mode expects raw bytes
    aes_cbc = pyaes.AESModeOfOperationCBC(aes_key, iv=iv)
    # pyaes CBC requires block-by-block encryption
    ciphertext = b""
    for i in range(0, len(padded), 16):
        ciphertext += aes_cbc.encrypt(padded[i : i + 16])

    # 3. RSA encrypt (key + IV) with router's pubkey
    rsa_pub = rsa.PublicKey(int(modulus_hex, 16), int(exponent_hex, 16))
    encrypted_key = rsa.encrypt(aes_key + iv, rsa_pub)

    # 4. Base64url encode (no padding)
    ct_b64 = base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode()
    ck_b64 = base64.urlsafe_b64encode(encrypted_key).rstrip(b"=").decode()

    logger.debug("Encrypted payload: ct=%d bytes, ck=%d bytes", len(ct_b64), len(ck_b64))
    return {"encrypted": "1", "ct": ct_b64, "ck": ck_b64}
