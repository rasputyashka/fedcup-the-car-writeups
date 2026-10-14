from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass

CHALLENGE_TTL_SECONDS = 30
MAX_CHALLENGE_LENGTH = 1024
DOMAIN = b"carhack-checker-v1\0"


def validate_identifier(value: str, max_length: int = 64) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise ValueError("invalid checker identity")
    if re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None:
        raise ValueError("invalid checker identity")
    return value


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("invalid challenge encoding")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid challenge encoding") from exc
    if _encode(raw) != value:
        raise ValueError("noncanonical challenge encoding")
    return raw


@dataclass(frozen=True)
class Challenge:
    team_id: str
    action: str
    subject: str
    expires_at: int
    nonce: bytes

    def __post_init__(self) -> None:
        validate_identifier(self.team_id)
        validate_identifier(self.subject, 256)
        if not isinstance(self.action, str) or self.action not in {"diag", "admin_provision"}:
            raise ValueError("invalid checker action")
        if type(self.expires_at) is not int or not 0 < self.expires_at < 2**53:
            raise ValueError("invalid challenge expiry")
        if not isinstance(self.nonce, bytes) or len(self.nonce) != 32:
            raise ValueError("invalid challenge nonce")

    def _payload(self) -> bytes:
        return json.dumps({
            "v": 1,
            "team_id": self.team_id,
            "action": self.action,
            "subject": self.subject,
            "expires_at": self.expires_at,
            "nonce": _encode(self.nonce),
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    def message(self) -> bytes:
        return DOMAIN + self._payload()

    def to_wire(self) -> str:
        return _encode(self._payload())

    @classmethod
    def from_wire(cls, value: str) -> "Challenge":
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_CHALLENGE_LENGTH:
            raise ValueError("invalid challenge length")
        try:
            fields = json.loads(_decode(value))
            if not isinstance(fields, dict) or set(fields) != {
                "v", "team_id", "action", "subject", "expires_at", "nonce"
            } or type(fields["v"]) is not int or fields["v"] != 1:
                raise ValueError("invalid challenge schema")
            proof = cls(fields["team_id"], fields["action"], fields["subject"],
                        fields["expires_at"], _decode(fields["nonce"]))
            if proof.to_wire() != value:
                raise ValueError("noncanonical challenge payload")
            return proof
        except (TypeError, KeyError, UnicodeError, ValueError) as exc:
            raise ValueError("invalid checker challenge") from exc
