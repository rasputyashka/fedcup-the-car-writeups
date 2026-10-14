from __future__ import annotations

import base64
import binascii
import os
import threading
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

try:
    from .checker_protocol import (
        Challenge,
        CHALLENGE_TTL_SECONDS,
        validate_identifier,
    )
except ImportError:
    from checker_protocol import (
        Challenge,
        CHALLENGE_TTL_SECONDS,
        validate_identifier,
    )


def _decode_key_or_signature(value: str, length: int) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("checker key or signature is required")
    candidate = value.strip()
    try:
        if len(candidate) == length * 2:
            decoded = bytes.fromhex(candidate)
        else:
            decoded = base64.b64decode(
                candidate + "=" * (-len(candidate) % 4),
                altchars=b"-_",
                validate=True,
            )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid checker key or signature") from exc
    if len(decoded) != length:
        raise ValueError("invalid checker key or signature length")
    return decoded


@dataclass(frozen=True)
class IssuedChallenge:
    value: str
    expires_in: int
    scope: str
    subject: str


@dataclass(frozen=True)
class _ChallengeRecord:
    proof: Challenge
    monotonic_deadline: float


class CheckerTrust:
    def __init__(
        self,
        public_key: str | bytes,
        *,
        team_id: str,
        ttl_seconds: int = CHALLENGE_TTL_SECONDS,
    ) -> None:
        self.team_id = validate_identifier(team_id)
        if (
            type(ttl_seconds) is not int
            or not 1 <= ttl_seconds <= CHALLENGE_TTL_SECONDS
        ):
            raise ValueError("invalid challenge lifetime")
        raw = (
            public_key
            if isinstance(public_key, bytes)
            else _decode_key_or_signature(public_key, 32)
        )
        self._verify_key = Ed25519PublicKey.from_public_bytes(raw)
        self._ttl_seconds = ttl_seconds
        self._records: dict[bytes, _ChallengeRecord] = {}
        self._lock = threading.RLock()

    @classmethod
    def from_environment(cls) -> "CheckerTrust":
        public_key, team_id = (
            os.environ.get("CHECKER_PUBKEY"),
            os.environ.get("TEAM_ID"),
        )
        if not public_key or not team_id:
            raise ValueError("CHECKER_PUBKEY and TEAM_ID are required")
        return cls(public_key, team_id=team_id)

    def issue(self, scope: str, subject: str) -> IssuedChallenge:
        proof = Challenge(
            self.team_id,
            scope,
            subject,
            int(time.time()) + self._ttl_seconds,
            os.urandom(32),
        )
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            self._records[proof.nonce] = _ChallengeRecord(
                proof, now + self._ttl_seconds
            )
        return IssuedChallenge(
            proof.to_wire(), self._ttl_seconds, scope, subject
        )

    def verify_and_consume(
        self, challenge: str, signature: str, *, scope: str, subject: str
    ) -> bool:
        try:
            proof = Challenge.from_wire(challenge)
            signature_bytes = _decode_key_or_signature(signature, 64)
        except ValueError:
            return False
        if (
            proof.team_id != self.team_id
            or proof.action != scope
            or proof.subject != subject
            or proof.expires_at <= time.time()
        ):
            return False
        with self._lock:
            self._discard_expired(time.monotonic())
            record = self._records.get(proof.nonce)
            if record is None or record.proof != proof:
                return False
            try:
                self._verify_key.verify(signature_bytes, proof.message())
            except (InvalidSignature, TypeError, ValueError):
                return False
            del self._records[proof.nonce]
            return True

    def _discard_expired(self, now: float) -> None:
        expired = [
            nonce
            for nonce, record in self._records.items()
            if record.monotonic_deadline <= now
        ]
        for nonce in expired:
            del self._records[nonce]
