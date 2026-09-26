"""Versioned encryption keys and an optional real ML-KEM-768/X25519 HPKE profile."""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.mlkem import MLKEM768PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, MLKEM768X25519PrivateKey, Suite

DEFAULT_PROFILE = "AES-256-GCM-v1"
HYBRID_PQ_PROFILE = "HYBRID-MLKEM768-X25519-HPKE-v1"
LEGACY_SIM_PROFILE = "HYBRID-PQ-SIM-v1"
_HYBRID_SUITE = Suite(KEM.MLKEM768_X25519, KDF.HKDF_SHA256, AEAD.AES_256_GCM)


class KeyRing:
    """Manages active and historical keys for data-at-rest encryption and rotation."""

    def __init__(self, primary_key_id: str, keys: dict[str, bytes], default_profile: str = DEFAULT_PROFILE):
        if not keys:
            raise ValueError("KeyRing must contain at least one key")
        if primary_key_id not in keys:
            raise ValueError(f"Primary key {primary_key_id} must exist in keys dictionary")
        for kid, kbytes in keys.items():
            if len(kbytes) != 32:
                raise ValueError(f"Key {kid} must be 32 bytes (AES-256)")
        self.primary_key_id = primary_key_id
        self.keys = keys
        self.default_profile = default_profile
        self.hybrid_private_keys: dict[str, MLKEM768X25519PrivateKey] = {}

    @classmethod
    def from_single_key(cls, key_b64: str, key_id: str = "k1") -> KeyRing:
        raw = base64.b64decode(key_b64, validate=True)
        return cls(primary_key_id=key_id, keys={key_id: raw})

    def add_key(self, key_id: str, key_b64: str, make_primary: bool = False) -> None:
        if key_id in self.keys:
            raise ValueError(f"Key ID {key_id} already exists; replacing it would orphan historical data")
        raw = base64.b64decode(key_b64, validate=True)
        if len(raw) != 32:
            raise ValueError(f"Key {key_id} must be 32 bytes")
        self.keys[key_id] = raw
        if make_primary:
            self.primary_key_id = key_id

    def add_hybrid_key_material(self, key_id: str, mlkem_seed_b64: str, x25519_private_b64: str) -> None:
        """Register independently generated private key material; keep it outside the database."""
        if key_id not in self.keys or key_id in self.hybrid_private_keys:
            raise ValueError("hybrid key requires an existing, unused key_id")
        mlkem_seed = base64.b64decode(mlkem_seed_b64, validate=True)
        x25519_raw = base64.b64decode(x25519_private_b64, validate=True)
        self.hybrid_private_keys[key_id] = MLKEM768X25519PrivateKey(
            MLKEM768PrivateKey.from_seed_bytes(mlkem_seed),
            X25519PrivateKey.from_private_bytes(x25519_raw),
        )

    def _hybrid_key(self, key_id: str) -> MLKEM768X25519PrivateKey:
        try:
            return self.hybrid_private_keys[key_id]
        except KeyError as exc:
            raise KeyError(f"hybrid private key for {key_id} is not configured") from exc

    def get_cipher(self, key_id: str) -> AESGCM:
        key = self.keys.get(key_id)
        if not key:
            raise KeyError(f"Key '{key_id}' not found in KeyRing. Available: {list(self.keys.keys())}")
        return AESGCM(key)

    def encrypt_envelope(
        self,
        raw_payload: bytes,
        aad: bytes,
        key_id: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        kid = key_id or self.primary_key_id
        profile = profile_id or self.default_profile
        if profile == DEFAULT_PROFILE:
            nonce = os.urandom(12)
            ciphertext = self.get_cipher(kid).encrypt(nonce, raw_payload, aad)
        elif profile == HYBRID_PQ_PROFILE:
            nonce = b""  # HPKE stores the encapsulation and AEAD ciphertext together.
            ciphertext = _HYBRID_SUITE.encrypt(
                raw_payload, self._hybrid_key(kid).public_key(), info=b"zds-event-v1:" + aad
            )
        else:
            raise ValueError(f"Unsupported crypto profile: {profile}")

        return {
            "key_id": kid,
            "crypto_profile_id": profile,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "kem_metadata": None,
        }

    def decrypt_envelope(
        self,
        key_id: str,
        profile_id: str,
        nonce: bytes,
        ciphertext: bytes,
        aad: bytes,
    ) -> bytes:
        if profile_id == DEFAULT_PROFILE or not profile_id:
            cipher = self.get_cipher(key_id)
            return cipher.decrypt(nonce, ciphertext, aad)
        if profile_id == HYBRID_PQ_PROFILE:
            return _HYBRID_SUITE.decrypt(
                ciphertext, self._hybrid_key(key_id), info=b"zds-event-v1:" + aad
            )
        if profile_id == LEGACY_SIM_PROFILE:
            # Read-only compatibility for data produced before the real HPKE profile existed.
            key = self.keys.get(key_id)
            if not key:
                raise KeyError(f"Key '{key_id}' not found for legacy simulation decrypt")
            pq_shared_secret = hashlib.sha3_256(key + nonce).digest()
            pq_cipher = AESGCM(pq_shared_secret)
            return pq_cipher.decrypt(nonce, ciphertext, aad)
        else:
            raise ValueError(f"Unknown crypto profile: {profile_id}")


def keyring_from_environment(key_b64: str) -> KeyRing:
    ring = KeyRing.from_single_key(key_b64)
    mlkem_seed = os.environ.get("QC_PQ_MLKEM_SEED_B64")
    x25519_private = os.environ.get("QC_PQ_X25519_PRIVATE_B64")
    if bool(mlkem_seed) != bool(x25519_private):
        raise ValueError("both PQ private key components must be configured")
    if mlkem_seed and x25519_private:
        ring.add_hybrid_key_material("k1", mlkem_seed, x25519_private)
    profile = os.environ.get("QC_CRYPTO_PROFILE", DEFAULT_PROFILE)
    if profile not in {DEFAULT_PROFILE, HYBRID_PQ_PROFILE}:
        raise ValueError("unsupported QC_CRYPTO_PROFILE")
    if profile == HYBRID_PQ_PROFILE and not mlkem_seed:
        raise ValueError("hybrid profile requires PQ private key components")
    ring.default_profile = profile
    return ring
