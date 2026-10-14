from __future__ import annotations

import asyncio
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path

try:
    from ..bus import CANBus
    from ..state import CAN_ABS_BRAKE, CANFrame, VehicleState
    from ..storage import RecordStore
except ImportError:
    from bus import CANBus
    from state import CAN_ABS_BRAKE, CANFrame, VehicleState
    from storage import RecordStore


MEMORY_SIZE = 0x4000
FLAG_REGION_BASE = 0x2000
MAX_READ = 512


class ABSRangeError(ValueError):
    pass


class ABSAccessError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class _TokenBinding:
    start: int
    end: int
    token: str

    def covers(self, address: int, end: int, token: str) -> bool:
        return self.start <= address and end <= self.end and hmac.compare_digest(
            self.token, token
        )


def _coerce_address(address: int | str) -> int:
    try:
        return int(address, 0) if isinstance(address, str) else int(address)
    except (TypeError, ValueError) as exc:
        raise ABSRangeError("address must be an integer or hexadecimal string") from exc


def _coerce_data(data: bytes | bytearray | memoryview | str) -> bytes:
    if isinstance(data, str):
        try:
            return bytes.fromhex(data)
        except ValueError as exc:
            raise ABSRangeError("string ABS data must be hexadecimal") from exc
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ABSRangeError("ABS data must be bytes-like")
    return bytes(data)


class ABSECU:

    name = "abs"
    LEGACY_READ_COMPAT = True
    BOUNDED_READ_WINDOW = False

    def __init__(
        self,
        bus: CANBus,
        state: VehicleState,
        *,
        memory_size: int = MEMORY_SIZE,
        flag_region_base: int = FLAG_REGION_BASE,
        max_read: int = MAX_READ,
        storage_dir: str | Path | None = None,
        retention_seconds: float | None = None,
    ) -> None:
        if memory_size <= 0:
            raise ValueError("memory_size must be positive")
        if not 0 <= flag_region_base < memory_size:
            raise ValueError("flag_region_base must be inside ABS memory")
        if max_read <= 0:
            raise ValueError("max_read must be positive")
        self.bus = bus
        self.state = state
        self.mem = bytearray(memory_size)
        self.memory_size = memory_size
        self.flag_region_base = flag_region_base
        self.max_read = max_read
        self._bindings: list[_TokenBinding] = []
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[CANFrame] | None = None
        self._stop_event = asyncio.Event()
        self._healthy = False
        self._records = RecordStore(
            Path(storage_dir) / "records.sqlite3" if storage_dir is not None else None,
            lifetime=retention_seconds,
        )
        self._records.prune("abs")
        for record in self._records.active("abs"):
            address, end = self._validate_write_range(int(record.key), len(record.data))
            if any(b.start < end and address < b.end for b in self._bindings):
                raise ValueError("overlapping persisted ABS records")
            self.mem[address:end] = record.data
            self._bindings.append(_TokenBinding(address, end, record.owner))

    async def ping(self) -> bool:

        return self._healthy and not self._stop_event.is_set()

    def stop(self) -> None:

        self._stop_event.set()

    def _validate_write_range(self, address: int, size: int) -> tuple[int, int]:
        if size <= 0:
            raise ABSRangeError("ABS writes cannot be empty")
        if address < 0 or address >= self.memory_size:
            raise ABSRangeError("ABS write address is outside memory")
        end = address + size
        if end > self.memory_size:
            raise ABSRangeError("ABS write extends outside memory")
        if self.BOUNDED_READ_WINDOW:
            if address < self.flag_region_base or size > self.max_read:
                raise ABSRangeError("ABS writes must stay in the protected flag region")
        return address, end

    def _read_end(self, address: int, length: int) -> int:
        if length < 0:
            raise ABSRangeError("ABS read length cannot be negative")
        if address < 0 or address >= self.memory_size:
            raise ABSRangeError("ABS read address is outside memory")
        if self.BOUNDED_READ_WINDOW and address < self.flag_region_base:
            raise ABSRangeError("ABS reads must stay in the protected flag region")
        limit = self.max_read if self.BOUNDED_READ_WINDOW else self.memory_size
        return min(address + length, address + limit, self.memory_size)

    @staticmethod
    def _new_or_supplied_token(wtok: str | None) -> str:
        token = str(wtok).strip() if wtok is not None else ""
        return token or secrets.token_urlsafe(24)

    async def write_abs(
        self,
        addr: int | str,
        data: bytes | bytearray | memoryview | str,
        wtok: str | None = None,
    ) -> str:

        address = _coerce_address(addr)
        payload = _coerce_data(data)
        address, end = self._validate_write_range(address, len(payload))
        token = self._new_or_supplied_token(wtok)
        async with self._lock:
            self._expire_records()
            overlapping = [
                binding for binding in self._bindings
                if binding.start < end and address < binding.end
            ]
            if overlapping:
                if len(overlapping) != 1 or not overlapping[0].covers(address, end, token):
                    raise ABSAccessError("ABS range is already reserved")
                binding = overlapping[0]
                updated = bytearray(self.mem[binding.start:binding.end])
                updated[address - binding.start:end - binding.start] = payload
                self._records.put("abs", str(binding.start), token, bytes(updated))
                self.mem[binding.start:binding.end] = updated
            else:
                self._commit_new_record(address, payload, token)
        await self.state.log(self.name, f"wrote {len(payload)} ABS bytes at {address:#x}")
        return token

    def _commit_new_record(self, address: int, payload: bytes, token: str) -> None:

        end = address + len(payload)
        self._records.put("abs", str(address), token, payload)
        self.mem[address:end] = payload
        self._bindings.append(_TokenBinding(address, end, token))

    def _expire_records(self) -> None:
        for record in self._records.prune("abs"):
            start = int(record.key)
            self.mem[start:start + len(record.data)] = bytes(len(record.data))
            self._bindings = [binding for binding in self._bindings if binding.start != start]

    def _free_address(self, size: int) -> int:

        address = self.flag_region_base
        for binding in sorted(self._bindings, key=lambda item: item.start):
            if binding.end <= address:
                continue
            if address + size <= binding.start:
                return address
            address = max(address, binding.end)
        if address + size <= self.memory_size:
            return address
        raise ABSRangeError("ABS storage is full")

    async def store_abs(self, data: bytes | bytearray | memoryview | str) -> tuple[int, str]:

        payload = _coerce_data(data)
        if not 0 < len(payload) <= self.max_read:
            raise ABSRangeError("ABS record size outside range")
        token = self._new_or_supplied_token(None)
        async with self._lock:
            self._expire_records()
            address = self._free_address(len(payload))
            self._commit_new_record(address, payload, token)
        await self.state.log(self.name, f"stored {len(payload)} ABS bytes at {address:#x}")
        return address, token

    async def read_abs(
        self,
        addr: int | str,
        length: int | str,
        wtok: str | None = None,
    ) -> bytes:

        address = _coerce_address(addr)
        try:
            requested = int(length, 0) if isinstance(length, str) else int(length)
        except (TypeError, ValueError) as exc:
            raise ABSRangeError("ABS read length must be an integer") from exc
        end = self._read_end(address, requested)
        token = "" if wtok is None else str(wtok).strip()

        async with self._lock:
            self._expire_records()
            if not (self.LEGACY_READ_COMPAT and not token):
                if not any(binding.covers(address, end, token) for binding in reversed(self._bindings)):
                    raise ABSAccessError("invalid ABS read token")
            result = bytes(self.mem[address:end])

        await self.state.log(self.name, f"read {len(result)} ABS bytes at {address:#x}")
        return result

    async def handle_frame(self, frame: CANFrame) -> None:

        if frame.id != CAN_ABS_BRAKE or not frame.data:
            return
        await self.state.set_controls(brake=frame.data[0])
        await self.state.log(self.name, f"brake pressure {frame.data[0]}")

    async def run(self, stop_event: asyncio.Event | None = None) -> None:

        self._queue = self.bus.subscribe(CAN_ABS_BRAKE)
        self._healthy = True
        await self.state.set_ecu_status(self.name, "ok")
        await self.state.log(self.name, "ABS ECU online")
        try:
            while not self._stop_event.is_set() and (
                stop_event is None or not stop_event.is_set()
            ):
                try:
                    frame = await asyncio.wait_for(self._queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                await self.handle_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._healthy = False
            await self.state.set_ecu_status(self.name, "down")
            raise
        finally:
            if self._queue is not None:
                self.bus.unsubscribe(self._queue)
                self._queue = None
            self._healthy = False


async def run(
    bus: CANBus,
    state: VehicleState,
    stop_event: asyncio.Event | None = None,
    **kwargs: object,
) -> None:

    await ABSECU(bus, state, **kwargs).run(stop_event)
