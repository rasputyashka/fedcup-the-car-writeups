from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping


CAN_ENGINE_THROTTLE = 0x100
CAN_ENGINE_PEDAL = 0x101
CAN_ENGINE_IGNITION = 0x120
CAN_ABS_BRAKE = 0x123
CAN_AUTOPILOT_STEERING = 0x200
CAN_AUTOPILOT_TURN_SIGNAL = 0x201
CAN_AUTOPILOT_HEADING = 0x300
CAN_TELEMETRY_SPEED = 0x400
CAN_DIAG_FLAG = 0x7DF
CAN_DIAG_TEST = 0x7FF

CAN_IDS: Mapping[str, int] = MappingProxyType(
    {
        "engine_throttle": CAN_ENGINE_THROTTLE,
        "engine_pedal": CAN_ENGINE_PEDAL,
        "engine_ignition": CAN_ENGINE_IGNITION,
        "abs_brake": CAN_ABS_BRAKE,
        "autopilot_steering": CAN_AUTOPILOT_STEERING,
        "autopilot_turn_signal": CAN_AUTOPILOT_TURN_SIGNAL,
        "autopilot_heading": CAN_AUTOPILOT_HEADING,
        "telemetry_speed": CAN_TELEMETRY_SPEED,
        "diag_flag": CAN_DIAG_FLAG,
        "diag_test": CAN_DIAG_TEST,
    }
)

DIAGNOSTIC_ID_MIN = 0x700
CAN_ID_MAX = 0x7FF
ECU_NAMES = ("infotainment", "abs", "engine", "autopilot", "telemetry")
ECU_STATUSES = frozenset(("ok", "hacked", "down"))
MAX_ECU_LOG_LINES = 20


def is_diagnostic_id(identifier: int) -> bool:

    return DIAGNOSTIC_ID_MIN <= int(identifier) <= CAN_ID_MAX


def clamp(value: float, lower: float, upper: float) -> float:

    if lower > upper:
        raise ValueError("lower bound must not exceed upper bound")
    return max(lower, min(upper, value))


def clamp_u8(value: int | float) -> int:

    return int(clamp(int(value), 0, 0xFF))


@dataclass(frozen=True, slots=True)
class CANFrame:

    id: int
    data: bytes
    ts: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        identifier = int(self.id)
        if not 0 <= identifier <= CAN_ID_MAX:
            raise ValueError(f"CAN id outside 11-bit range: {identifier!r}")

        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError("CAN frame data must be bytes-like")
        payload = bytes(self.data)
        if len(payload) > 8:
            raise ValueError("a CAN frame payload cannot exceed 8 bytes")

        timestamp = float(self.ts)
        if not math.isfinite(timestamp):
            raise ValueError("CAN frame timestamp must be finite")

        object.__setattr__(self, "id", identifier)
        object.__setattr__(self, "data", payload)
        object.__setattr__(self, "ts", timestamp)

    @property
    def identifier(self) -> int:

        return self.id

    def as_tuple(self) -> tuple[int, bytes, float]:

        return self.id, self.data, self.ts


def _default_ecus() -> dict[str, str]:
    return {name: "ok" for name in ECU_NAMES}


def _default_logs() -> dict[str, list[str]]:
    return {name: [] for name in ECU_NAMES}


@dataclass(slots=True)
class VehicleState:

    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    steering: float = 0.0
    throttle: int = 0
    brake: int = 0
    turn_signal: int = 0
    on_track: bool = True
    ignition: bool = True
    ecus: dict[str, str] = field(default_factory=_default_ecus)
    ecu_logs: dict[str, list[str]] = field(default_factory=_default_logs)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.x = float(self.x)
        self.y = float(self.y)
        self.heading = float(self.heading)
        self.speed = max(0.0, float(self.speed))
        self.steering = float(clamp(float(self.steering), -0.6, 0.6))
        self.throttle = clamp_u8(self.throttle)
        self.brake = clamp_u8(self.brake)
        self.turn_signal = int(clamp(int(self.turn_signal), 0, 2))
        self.ignition = bool(self.ignition)
        self.on_track = bool(self.on_track)

        normalized_statuses = _default_ecus()
        for name, status in self.ecus.items():
            if name in normalized_statuses and status in ECU_STATUSES:
                normalized_statuses[name] = status
        self.ecus = normalized_statuses

        normalized_logs = _default_logs()
        for name, lines in self.ecu_logs.items():
            if name in normalized_logs:
                normalized_logs[name] = [str(line) for line in lines][-MAX_ECU_LOG_LINES:]
        self.ecu_logs = normalized_logs

    def _set_values(self, values: Mapping[str, Any]) -> None:
        allowed = {
            "x",
            "y",
            "heading",
            "speed",
            "steering",
            "throttle",
            "brake",
            "turn_signal",
            "on_track",
            "ignition",
        }
        unknown = set(values).difference(allowed)
        if unknown:
            raise KeyError(f"unsupported vehicle-state fields: {sorted(unknown)!r}")

        if "x" in values:
            self.x = float(values["x"])
        if "y" in values:
            self.y = float(values["y"])
        if "heading" in values:
            self.heading = float(values["heading"])
        if "speed" in values:
            self.speed = max(0.0, float(values["speed"]))
        if "steering" in values:
            self.steering = float(clamp(float(values["steering"]), -0.6, 0.6))
        if "throttle" in values:
            self.throttle = clamp_u8(values["throttle"])
        if "brake" in values:
            self.brake = clamp_u8(values["brake"])
        if "turn_signal" in values:
            self.turn_signal = int(clamp(int(values["turn_signal"]), 0, 2))
        if "on_track" in values:
            self.on_track = bool(values["on_track"])
        if "ignition" in values:
            self.ignition = bool(values["ignition"])

    async def update(self, **values: Any) -> None:

        async with self._lock:
            self._set_values(values)

    async def set_controls(
        self,
        *,
        throttle: int | None = None,
        brake: int | None = None,
        steering: float | None = None,
        turn_signal: int | None = None,
        ignition: bool | None = None,
    ) -> None:

        values: dict[str, Any] = {}
        if throttle is not None:
            values["throttle"] = throttle
        if brake is not None:
            values["brake"] = brake
        if steering is not None:
            values["steering"] = steering
        if turn_signal is not None:
            values["turn_signal"] = turn_signal
        if ignition is not None:
            values["ignition"] = ignition
        if values:
            await self.update(**values)

    async def apply_physics(
        self,
        *,
        x: float,
        y: float,
        heading: float,
        speed: float,
        on_track: bool,
    ) -> None:

        await self.update(
            x=x,
            y=y,
            heading=heading,
            speed=speed,
            on_track=on_track,
        )

    async def set_ecu_status(self, ecu: str, status: str) -> None:

        if ecu not in ECU_NAMES:
            raise KeyError(f"unknown ECU: {ecu}")
        if status not in ECU_STATUSES:
            raise ValueError(f"invalid ECU status: {status}")
        async with self._lock:
            self.ecus[ecu] = status

    async def log(self, ecu: str, message: object) -> None:

        if ecu not in ECU_NAMES:
            raise KeyError(f"unknown ECU: {ecu}")
        async with self._lock:
            lines = self.ecu_logs[ecu]
            lines.append(str(message))
            del lines[:-MAX_ECU_LOG_LINES]

    append_log = log
    append_ecu_log = log

    def to_dict(self) -> dict[str, Any]:

        return {
            "x": self.x,
            "y": self.y,
            "heading": self.heading,
            "speed": self.speed,
            "steering": self.steering,
            "throttle": self.throttle,
            "brake": self.brake,
            "turn_signal": self.turn_signal,
            "on_track": self.on_track,
            "ignition": self.ignition,
            "ecus": dict(self.ecus),
            "ecu_logs": {name: list(lines) for name, lines in self.ecu_logs.items()},
        }

    serialize = to_dict

    async def snapshot(self) -> dict[str, Any]:

        async with self._lock:
            return self.to_dict()

    async def replace_logs(self, ecu: str, lines: Iterable[object]) -> None:

        if ecu not in ECU_NAMES:
            raise KeyError(f"unknown ECU: {ecu}")
        async with self._lock:
            self.ecu_logs[ecu] = [str(line) for line in lines][-MAX_ECU_LOG_LINES:]
