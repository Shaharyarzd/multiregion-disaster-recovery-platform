"""Local ECDSA evidence test double matching the AWS KMS digest contract."""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils


class LocalEcdsaEvidenceKey:
    """P-256 signer/verifier for tests; it does not claim AWS KMS behavior."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None) -> None:
        self.private_key = private_key or ec.generate_private_key(ec.SECP256R1())
        public_der = self.private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.key_id = "local-p256:" + hashlib.sha256(public_der).hexdigest()[:24]

    def sign_digest(self, digest_hex: str) -> dict[str, str]:
        signature = self.private_key.sign(
            bytes.fromhex(digest_hex), ec.ECDSA(utils.Prehashed(hashes.SHA256()))
        )
        return {
            "algorithm": "ECDSA_SHA_256",
            "key_id": self.key_id,
            "signature_base64": base64.b64encode(signature).decode(),
        }

    def verify_digest(self, digest_hex: str, signature: dict[str, str]) -> bool:
        if signature.get("algorithm") != "ECDSA_SHA_256" or signature.get("key_id") != self.key_id:
            return False
        try:
            self.private_key.public_key().verify(
                base64.b64decode(signature["signature_base64"], validate=True),
                bytes.fromhex(digest_hex),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        except (InvalidSignature, ValueError, KeyError):
            return False
        return True
