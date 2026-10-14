from __future__ import annotations

import asyncio
import base64
import hmac
import inspect
import math
import secrets
import struct
import time
from dataclasses import dataclass
from typing import Any
from pathlib import Path

try:
    from ..storage import Record, RecordStore
except ImportError:
    from storage import Record, RecordStore

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


CAN_ID_TELEMETRY = 0x400
CAN_ID_DIAGNOSTIC_FLAG = 0x7DF
_DIAG_CHUNK_SIZE = 7
_BLACKBOX_AAD = b"carhack:blackbox:v1"

DEFAULT_TELE_KEY = b"CarHack-telemetry-sea-key-v1-32b"


@dataclass(frozen=True)
class DiagnosticFlagInfo:
    base_seq: int
    chunk_count: int
    record_id: str


@dataclass(frozen=True)
class _DiagnosticFlag:
    record_id: str
    data: bytes
    base_seq: int
    chunk_count: int


class TelemetryError(RuntimeError):
    pass


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class TelemetryECU:
    name = "telemetry"

    def __init__(
        self,
        bus: Any,
        state: Any | None = None,
        *,
        storage_dir: str | Path | None = None,
        retention_seconds: float | None = None,
        telemetry_key: bytes | None = None,
    ) -> None:
        key = (
            DEFAULT_TELE_KEY if telemetry_key is None else bytes(telemetry_key)
        )
        if len(key) not in (16, 24, 32):
            raise ValueError(
                "AES-GCM telemetry key must be 16, 24, or 32 bytes"
            )

        self.bus = bus
        self.state = state
        self._cipher = AESGCM(key)
        self._records = RecordStore(
            Path(storage_dir) / "records.sqlite3"
            if storage_dir is not None
            else None,
            lifetime=retention_seconds,
        )
        self._lock = asyncio.Lock()
        self._next_sequence = secrets.randbelow(256)
        self._stop_event = asyncio.Event()
        self._healthy = False

    async def ping(self) -> bool:
        return self._healthy and not self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    async def store_diagnostic_flag(self, flag: str) -> DiagnosticFlagInfo:
        if not isinstance(flag, str) or not flag:
            raise TelemetryError("diagnostic flag must be a non-empty string")
        raw = flag.encode("utf-8")
        chunk_count = math.ceil(len(raw) / _DIAG_CHUNK_SIZE)
        if chunk_count > 64:
            raise TelemetryError("diagnostic flag is too large")

        async with self._lock:
            base_seq = self._next_sequence
            self._next_sequence = (self._next_sequence + chunk_count) % 256
            record = _DiagnosticFlag(
                record_id=secrets.token_urlsafe(12),
                data=raw,
                base_seq=base_seq,
                chunk_count=chunk_count,
            )
            self._records.prune("diag")
            self._records.put(
                "diag", record.record_id, "", bytes([base_seq]) + raw
            )

        await self._broadcast_record(record)
        await self._log("stored diagnostic flag frames")
        return DiagnosticFlagInfo(
            base_seq=base_seq,
            chunk_count=chunk_count,
            record_id=record.record_id,
        )

    async def diagnostic_frames(
        self, base_seq: int, chunk_count: int
    ) -> list[bytes]:
        async with self._lock:
            for stored in self._records.active("diag"):
                record = self._diagnostic_record(stored)
                if (
                    record.base_seq == base_seq
                    and record.chunk_count == chunk_count
                ):
                    return self._frames_for(record)
        return []

    @staticmethod
    def _diagnostic_record(stored: Record) -> _DiagnosticFlag:
        raw = stored.data[1:]
        return _DiagnosticFlag(
            stored.key,
            raw,
            stored.data[0],
            math.ceil(len(raw) / _DIAG_CHUNK_SIZE),
        )

    async def get_diagnostic_frames(
        self, record_id: str
    ) -> list[bytes] | None:
        async with self._lock:
            stored = self._records.get("diag", record_id)
        return (
            None
            if stored is None
            else self._frames_for(self._diagnostic_record(stored))
        )

    async def store_blackbox(self, flag: str, owner: str) -> str:
        if not isinstance(flag, str) or not flag:
            raise TelemetryError("black-box flag must be a non-empty string")
        if not isinstance(owner, str) or not owner:
            raise TelemetryError("black-box owner is required")

        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(
            nonce, flag.encode("utf-8"), _BLACKBOX_AAD
        )
        record_id = secrets.token_urlsafe(18)
        async with self._lock:
            self._records.prune("blackbox")
            self._records.put("blackbox", record_id, owner, nonce + ciphertext)
        await self._log("stored encrypted black-box record")
        return record_id

    async def get_blackbox(self, record_id: str, owner: str) -> str | None:
        async with self._lock:
            record = self._records.get("blackbox", record_id)
        if record is None or not hmac.compare_digest(record.owner, owner):
            return None
        try:
            plaintext = self._cipher.decrypt(
                record.data[:12], record.data[12:], _BLACKBOX_AAD
            )
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise TelemetryError(
                "black-box record cannot be decrypted"
            ) from exc

    async def get_blackbox_raw(self, record_id: str) -> str | None:
        async with self._lock:
            record = self._records.get("blackbox", record_id)
        if record is None:
            return None
        return base64.b64encode(record.data).decode("ascii")

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        await self._set_status("ok")
        self._healthy = True
        next_beacon = time.monotonic()
        next_diagnostic = next_beacon
        try:
            while not self._stop_event.is_set() and (
                stop_event is None or not stop_event.is_set()
            ):
                now = time.monotonic()
                if now >= next_beacon:
                    await self._publish_telemetry_beacon()
                    next_beacon = now + 0.2
                if now >= next_diagnostic:
                    await self._broadcast_active_records()
                    next_diagnostic = now + 1.0

                delay = max(
                    0.01, min(next_beacon, next_diagnostic) - time.monotonic()
                )
                if stop_event is None:
                    await asyncio.sleep(delay)
                else:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(), timeout=delay
                        )
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self._healthy = False
            await self._set_status("down")
            raise
        finally:
            self._healthy = False

    def _frames_for(self, record: _DiagnosticFlag) -> list[bytes]:
        frames: list[bytes] = []
        for index in range(record.chunk_count):
            chunk = record.data[
                index * _DIAG_CHUNK_SIZE : (index + 1) * _DIAG_CHUNK_SIZE
            ]
            sequence = (record.base_seq + index) % 256
            frames.append(
                bytes((sequence,)) + chunk.ljust(_DIAG_CHUNK_SIZE, b"\0")
            )
        return frames

    async def _broadcast_record(self, record: _DiagnosticFlag) -> None:
        for frame in self._frames_for(record):
            await self._publish_diagnostic_frame(frame)

    async def _broadcast_active_records(self) -> None:
        async with self._lock:
            records = [
                self._diagnostic_record(stored)
                for stored in self._records.active("diag")
            ]
        for record in records:
            await self._broadcast_record(record)

    async def _publish_diagnostic_frame(self, payload: bytes) -> None:
        await self._publish(
            CAN_ID_DIAGNOSTIC_FLAG, payload, record_in_ring=True
        )
        await self._record_diagnostic_history(CAN_ID_DIAGNOSTIC_FLAG, payload)

    async def _publish_telemetry_beacon(self) -> None:
        speed = float(getattr(self.state, "speed", 0.0)) if self.state else 0.0
        heading = (
            float(getattr(self.state, "heading", 0.0)) if self.state else 0.0
        )
        speed_raw = max(0, min(0xFFFF, round(speed * 100)))
        heading_raw = max(
            -0x8000, min(0x7FFF, round(math.degrees(heading) * 100))
        )
        await self._publish(
            CAN_ID_TELEMETRY,
            struct.pack("<Hh", speed_raw, heading_raw),
            record_in_ring=True,
        )

    async def _publish(
        self, can_id: int, payload: bytes, *, record_in_ring: bool
    ) -> None:
        publish = getattr(self.bus, "publish", None)
        if publish is None:
            raise TelemetryError("CAN bus has no publish method")

        try:
            parameters = inspect.signature(publish).parameters
        except (TypeError, ValueError):
            parameters = {}
        for name in (
            "record_in_ring",
            "include_in_ring",
            "sniffable",
            "public",
        ):
            if name in parameters:
                await _await_if_needed(
                    publish(can_id, payload, **{name: record_in_ring})
                )
                return
        await _await_if_needed(publish(can_id, payload))

    async def _record_diagnostic_history(
        self, can_id: int, payload: bytes
    ) -> None:
        for name in (
            "record_frame",
            "mirror_to_sniff_ring",
            "record_public_frame",
        ):
            mirror = getattr(self.bus, name, None)
            if mirror is not None:
                await _await_if_needed(mirror(can_id, payload))
                return

        ring = getattr(self.bus, "ring", None)
        if ring is not None and hasattr(ring, "append"):
            ring.append((can_id, payload, time.monotonic()))

    async def _set_status(self, status: str) -> None:
        if self.state is None:
            return
        setter = getattr(self.state, "set_ecu_status", None)
        if setter is not None:
            await _await_if_needed(setter("telemetry", status))
            return
        ecus = getattr(self.state, "ecus", None)
        if isinstance(ecus, dict):
            ecus["telemetry"] = status

    async def _log(self, message: str) -> None:
        if self.state is None:
            return
        logger = getattr(self.state, "append_ecu_log", None)
        if logger is not None:
            await _await_if_needed(logger("telemetry", message))
            return
        logs = getattr(self.state, "ecu_logs", None)
        if isinstance(logs, dict):
            logs.setdefault("telemetry", []).append(message)
            del logs["telemetry"][:-20]
