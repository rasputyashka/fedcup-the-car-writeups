from __future__ import annotations

import asyncio
import math
import struct
import time

try:
    from ..bus import CANBus
    from ..physics import next_waypoint
    from ..state import (
        CAN_AUTOPILOT_HEADING,
        CAN_AUTOPILOT_STEERING,
        CAN_AUTOPILOT_TURN_SIGNAL,
        CANFrame,
        VehicleState,
        clamp,
    )
except ImportError:
    from bus import CANBus
    from physics import next_waypoint
    from state import (
        CAN_AUTOPILOT_HEADING,
        CAN_AUTOPILOT_STEERING,
        CAN_AUTOPILOT_TURN_SIGNAL,
        CANFrame,
        VehicleState,
        clamp,
    )


class AutopilotECU:
    name = "autopilot"

    def __init__(
        self,
        bus: CANBus,
        state: VehicleState,
        *,
        control_interval: float = 0.05,
        manual_override_seconds: float = 0.50,
    ) -> None:
        if control_interval <= 0:
            raise ValueError("control_interval must be positive")
        if manual_override_seconds <= 0:
            raise ValueError("manual_override_seconds must be positive")
        self.bus = bus
        self.state = state
        self.control_interval = float(control_interval)
        self.manual_override_seconds = float(manual_override_seconds)
        self._manual_steering: tuple[float, float] | None = None
        self._queue: asyncio.Queue[CANFrame] | None = None
        self._stop_event = asyncio.Event()
        self._healthy = False

    async def ping(self) -> bool:

        return self._healthy and not self._stop_event.is_set()

    def stop(self) -> None:

        self._stop_event.set()

    @staticmethod
    def _decode_steering(raw: int) -> float:
        signed = raw if raw < 0x80 else raw - 0x100
        return float(clamp(signed / 127.0 * 0.6, -0.6, 0.6))

    async def handle_frame(self, frame: CANFrame) -> None:

        if not frame.data:
            await self.state.log(
                self.name, f"ignored empty frame {frame.id:#x}"
            )
            return
        if frame.id == CAN_AUTOPILOT_STEERING:
            target = self._decode_steering(frame.data[0])
            self._manual_steering = (target, frame.ts)
            await self.state.set_controls(steering=target)
            await self.state.log(
                self.name, f"manual steering target {target:.3f}"
            )
        elif frame.id == CAN_AUTOPILOT_TURN_SIGNAL:
            signal = int(clamp(frame.data[0], 0, 2))
            await self.state.set_controls(turn_signal=signal)
            await self.state.log(self.name, f"turn signal {signal}")

    @staticmethod
    def _angle_difference(target: float, current: float) -> float:
        return math.atan2(
            math.sin(target - current), math.cos(target - current)
        )

    async def _control_tick(self) -> None:
        snapshot = await self.state.snapshot()
        now = time.monotonic()

        steering: float
        if (
            self._manual_steering is not None
            and now - self._manual_steering[1] <= self.manual_override_seconds
        ):
            steering = self._manual_steering[0]
        else:
            self._manual_steering = None
            target_x, target_y = next_waypoint(
                float(snapshot["x"]),
                float(snapshot["y"]),
                lookahead=2,
            )
            desired_heading = math.atan2(
                target_y - float(snapshot["y"]),
                target_x - float(snapshot["x"]),
            )
            error = self._angle_difference(
                desired_heading, float(snapshot["heading"])
            )
            steering = float(clamp(error * 0.90, -0.6, 0.6))

        await self.state.set_controls(steering=steering)
        heading_milliradians = int(
            clamp(round(float(snapshot["heading"]) * 1000.0), -0x8000, 0x7FFF)
        )
        await self.bus.publish(
            CAN_AUTOPILOT_HEADING,
            struct.pack("<h", heading_milliradians),
        )

    async def run(self, stop_event: asyncio.Event | None = None) -> None:

        self._queue = self.bus.subscribe(
            (CAN_AUTOPILOT_STEERING, CAN_AUTOPILOT_TURN_SIGNAL)
        )
        self._healthy = True
        await self.state.set_ecu_status(self.name, "ok")
        await self.state.log(self.name, "autopilot ECU online")
        next_control = time.monotonic()
        try:
            while not self._stop_event.is_set() and (
                stop_event is None or not stop_event.is_set()
            ):
                timeout = max(0.001, next_control - time.monotonic())
                try:
                    frame = await asyncio.wait_for(
                        self._queue.get(), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    frame = None
                if frame is not None:
                    await self.handle_frame(frame)

                now = time.monotonic()
                if now >= next_control:
                    await self._control_tick()
                    next_control = now + self.control_interval
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

    await AutopilotECU(bus, state, **kwargs).run(stop_event)
