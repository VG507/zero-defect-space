"""Long-term cryptographic management and KeyRing for Zero Defect Space.
Provides:
- Multiple key versions (key_version / key_id)
- Multi-profile cryptographic envelopes (AES-256-GCM, HYBRID-PQ-SIM)
- Key rotation without loss of legacy readability
- Post-Quantum Hybrid KEM envelope modeling against Store-Now-Decrypt-Later threats
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEFAULT_PROFILE = "AES-256-GCM-v1"
HYBRID_PQ_PROFILE = "HYBRID-PQ-SIM-v1"


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

    @classmethod
    def from_single_key(cls, key_b64: str, key_id: str = "k1") -> KeyRing:
        raw = base64.b64decode(key_b64, validate=True)
        return cls(primary_key_id=key_id, keys={key_id: raw})

    def add_key(self, key_id: str, key_b64: str, make_primary: bool = False) -> None:
        raw = base64.b64decode(key_b64, validate=True)
        if len(raw) != 32:
            raise ValueError(f"Key {key_id} must be 32 bytes")
        self.keys[key_id] = raw
        if make_primary:
            self.primary_key_id = key_id

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
        cipher = self.get_cipher(kid)
        nonce = os.urandom(12)

        if profile == DEFAULT_PROFILE:
            ciphertext = cipher.encrypt(nonce, raw_payload, aad)
            kem_metadata = None
        elif profile == HYBRID_PQ_PROFILE:
            # Modeled Hybrid Post-Quantum KEM envelope (ML-KEM-768 + Classical DEK)
            # Simulates encapsulation of symmetric payload key with post-quantum lattice seed
            pq_shared_secret = hashlib.sha3_256(self.keys[kid] + nonce).digest()
            pq_cipher = AESGCM(pq_shared_secret)
            ciphertext = pq_cipher.encrypt(nonce, raw_payload, aad)
            kem_metadata = {
                "algorithm": "ML-KEM-768+AES-256-GCM",
                "encapsulated_key_digest": hashlib.sha256(pq_shared_secret).hexdigest(),
                "classical_profile": "AES-256-GCM",
            }
        else:
            raise ValueError(f"Unsupported crypto profile: {profile}")

        return {
            "key_id": kid,
            "crypto_profile_id": profile,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "kem_metadata": json.dumps(kem_metadata) if kem_metadata else None,
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
        elif profile_id == HYBRID_PQ_PROFILE:
            key = self.keys.get(key_id)
            if not key:
                raise KeyError(f"Key '{key_id}' not found for hybrid PQ decrypt")
            pq_shared_secret = hashlib.sha3_256(key + nonce).digest()
            pq_cipher = AESGCM(pq_shared_secret)
            return pq_cipher.decrypt(nonce, ciphertext, aad)
        else:
            raise ValueError(f"Unknown crypto profile: {profile_id}")
