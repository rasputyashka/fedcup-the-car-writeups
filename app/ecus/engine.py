from __future__ import annotations

import asyncio
import time

try:
    from ..bus import CANBus
    from ..state import (
        CAN_ENGINE_IGNITION,
        CAN_ENGINE_PEDAL,
        CAN_ENGINE_THROTTLE,
        CANFrame,
        VehicleState,
        clamp_u8,
    )
except ImportError:
    from bus import CANBus
    from state import (
        CAN_ENGINE_IGNITION,
        CAN_ENGINE_PEDAL,
        CAN_ENGINE_THROTTLE,
        CANFrame,
        VehicleState,
        clamp_u8,
    )


class EngineECU:

    name = "engine"
    PEDAL_SENSOR_AUTHORITY = True

    def __init__(
        self,
        bus: CANBus,
        state: VehicleState,
        *,
        default_throttle: int = 96,
        pedal_timeout: float = 0.75,
    ) -> None:
        if pedal_timeout <= 0:
            raise ValueError("pedal_timeout must be positive")
        self.bus = bus
        self.state = state
        self.default_throttle = clamp_u8(default_throttle)
        self.pedal_timeout = float(pedal_timeout)
        self._command_throttle = self.default_throttle
        self._pedal: tuple[int, float] | None = None
        self._ignition = True
        self._queue: asyncio.Queue[CANFrame] | None = None
        self._stop_event = asyncio.Event()
        self._healthy = False

    async def ping(self) -> bool:

        return self._healthy and not self._stop_event.is_set()

    def stop(self) -> None:

        self._stop_event.set()

    async def handle_frame(self, frame: CANFrame) -> None:

        if not frame.data:
            await self.state.log(self.name, f"ignored empty frame {frame.id:#x}")
            return

        if frame.id == CAN_ENGINE_THROTTLE:
            self._command_throttle = frame.data[0]
            await self.state.log(self.name, f"throttle command {self._command_throttle}")
        elif frame.id == CAN_ENGINE_PEDAL:
            self._pedal = (frame.data[0], frame.ts)
            await self.state.log(self.name, f"pedal sensor {frame.data[0]}")
        elif frame.id == CAN_ENGINE_IGNITION:
            self._ignition = bool(frame.data[0])
            await self.state.log(
                self.name,
                "ignition on" if self._ignition else "ignition off",
            )

        await self._apply_effective_throttle()

    def _effective_throttle(self) -> int:
        if self.PEDAL_SENSOR_AUTHORITY and self._pedal is not None:
            value, timestamp = self._pedal
            if time.monotonic() - timestamp <= self.pedal_timeout:
                return value
        return self._command_throttle

    async def _apply_effective_throttle(self) -> None:
        throttle = self._effective_throttle()
        await self.state.set_controls(throttle=throttle, ignition=self._ignition)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:

        self._queue = self.bus.subscribe(
            (CAN_ENGINE_THROTTLE, CAN_ENGINE_PEDAL, CAN_ENGINE_IGNITION)
        )
        self._healthy = True
        await self.state.set_ecu_status(self.name, "ok")
        await self.state.log(self.name, "engine ECU online")
        await self._apply_effective_throttle()
        try:
            while not self._stop_event.is_set() and (
                stop_event is None or not stop_event.is_set()
            ):
                try:
                    frame = await asyncio.wait_for(self._queue.get(), timeout=0.10)
                except asyncio.TimeoutError:
                    await self._apply_effective_throttle()
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

    await EngineECU(bus, state, **kwargs).run(stop_event)
