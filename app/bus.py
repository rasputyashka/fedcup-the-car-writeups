from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Iterable

try:
    from .state import CANFrame, CAN_ID_MAX, is_diagnostic_id
except ImportError:
    from state import CANFrame, CAN_ID_MAX, is_diagnostic_id


@dataclass(frozen=True, slots=True)
class _Subscriber:
    ids: frozenset[int] | None
    diag_ok: bool


class CANBus:

    def __init__(
        self,
        *,
        ring_size: int = 4096,
        subscriber_queue_size: int = 256,
        mirror_diagnostic_history: bool = True,
    ) -> None:
        if ring_size <= 0:
            raise ValueError("ring_size must be positive")
        if subscriber_queue_size <= 0:
            raise ValueError("subscriber_queue_size must be positive")

        self.ring: deque[CANFrame] = deque(maxlen=ring_size)
        self._history: deque[CANFrame] = deque(maxlen=ring_size)
        self.subscribers: dict[asyncio.Queue[CANFrame], _Subscriber] = {}
        self._subscriber_queue_size = subscriber_queue_size
        self.mirror_diagnostic_history = mirror_diagnostic_history
        self._lock = asyncio.Lock()

    @staticmethod
    def _normalize_ids(ids: Iterable[int] | int | None) -> frozenset[int] | None:
        if ids is None:
            return None
        values = (ids,) if isinstance(ids, int) else tuple(ids)
        normalized = frozenset(int(value) for value in values)
        if any(identifier < 0 or identifier > CAN_ID_MAX for identifier in normalized):
            raise ValueError("subscription contains a non-11-bit CAN id")
        return normalized

    @staticmethod
    def _visible(identifier: int, subscriber: _Subscriber) -> bool:
        if is_diagnostic_id(identifier) and not subscriber.diag_ok:
            return False
        return subscriber.ids is None or identifier in subscriber.ids

    def subscribe(
        self,
        ids: Iterable[int] | int | None = None,
        *,
        diag_ok: bool = False,
        maxsize: int | None = None,
    ) -> asyncio.Queue[CANFrame]:

        capacity = self._subscriber_queue_size if maxsize is None else int(maxsize)
        if capacity <= 0:
            raise ValueError("maxsize must be positive")
        queue: asyncio.Queue[CANFrame] = asyncio.Queue(maxsize=capacity)
        self.subscribers[queue] = _Subscriber(
            ids=self._normalize_ids(ids),
            diag_ok=bool(diag_ok),
        )
        return queue

    def unsubscribe(self, queue: asyncio.Queue[CANFrame]) -> None:

        self.subscribers.pop(queue, None)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[CANFrame], frame: CANFrame) -> None:
        try:
            queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            queue.put_nowait(frame)
        except asyncio.QueueFull:
            return

    async def publish(
        self,
        identifier: int,
        data: bytes | bytearray | memoryview = b"",
        *,
        record_in_ring: bool | None = None,
    ) -> CANFrame:

        if record_in_ring is not None and not isinstance(record_in_ring, bool):
            raise TypeError("record_in_ring must be a boolean when supplied")
        frame = CANFrame(int(identifier), bytes(data))
        async with self._lock:
            self._history.append(frame)
            if self.mirror_diagnostic_history or not is_diagnostic_id(frame.id):
                self.ring.append(frame)
            subscribers = tuple(self.subscribers.items())

        for queue, subscriber in subscribers:
            if self._visible(frame.id, subscriber):
                self._enqueue(queue, frame)
        return frame

    def record_frame(
        self,
        identifier: int,
        data: bytes | bytearray | memoryview = b"",
    ) -> CANFrame:

        frame = CANFrame(int(identifier), bytes(data))
        if self.ring and self.ring[-1].id == frame.id and self.ring[-1].data == frame.data:
            return self.ring[-1]
        self.ring.append(frame)
        return frame

    def recent(
        self,
        identifier: int | None = None,
        n: int = 1,
        *,
        diag_ok: bool = False,
        id: int | None = None,
    ) -> list[CANFrame]:

        if id is not None:
            if identifier is not None and int(identifier) != int(id):
                raise ValueError("identifier and id disagree")
            identifier = id
        if n < 0:
            raise ValueError("n must not be negative")
        if identifier is not None:
            identifier = int(identifier)
            if not 0 <= identifier <= CAN_ID_MAX:
                raise ValueError("CAN id outside 11-bit range")
            if is_diagnostic_id(identifier) and not diag_ok:
                raise PermissionError("diagnostic authorization required")

        frames = [
            frame
            for frame in self._history
            if identifier is None or frame.id == identifier
        ]
        if identifier is None and not diag_ok:
            frames = [frame for frame in frames if not is_diagnostic_id(frame.id)]
        return frames[-n:] if n else []

    def sniff(self, n: int = 4096, *, diag_ok: bool = False) -> list[CANFrame]:

        if n < 0:
            raise ValueError("n must not be negative")
        source = self._history if diag_ok else self.ring
        selected = list(source)
        return selected[-n:] if n else []

    def clear(self) -> None:

        self.ring.clear()
        self._history.clear()
