from __future__ import annotations

import asyncio
import math
import time
from typing import Sequence

try:
    from .state import VehicleState, clamp
except ImportError:
    from state import VehicleState, clamp


TICK_HZ = 20.0
TICK_SECONDS = 1.0 / TICK_HZ
WHEELBASE = 2.6
A_MAX = 3.5
B_MAX = 8.5
DRAG = 0.18
V_MAX = 17.0
TRACK_BAND_WIDTH = 6.0

TRACK: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (11.0, 0.0),
    (22.0, 0.0),
    (33.0, 1.0),
    (43.0, 4.0),
    (51.0, 9.0),
    (57.0, 17.0),
    (60.0, 27.0),
    (60.0, 38.0),
    (58.0, 48.0),
    (53.0, 57.0),
    (45.0, 63.0),
    (34.0, 67.0),
    (22.0, 69.0),
    (10.0, 69.0),
    (-1.0, 67.0),
    (-10.0, 62.0),
    (-16.0, 54.0),
    (-19.0, 44.0),
    (-20.0, 33.0),
    (-19.0, 22.0),
    (-15.0, 13.0),
    (-9.0, 6.0),
    (-3.0, 2.0),
)


def _distance_to_segment(
    x: float,
    y: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(x - ax, y - ay)
    projection = ((x - ax) * dx + (y - ay) * dy) / length_sq
    projection = clamp(projection, 0.0, 1.0)
    px, py = ax + projection * dx, ay + projection * dy
    return math.hypot(x - px, y - py)


def in_band(
    x: float,
    y: float,
    width: float = TRACK_BAND_WIDTH,
    *,
    track: Sequence[tuple[float, float]] = TRACK,
) -> bool:

    if width < 0:
        raise ValueError("track width must not be negative")
    if len(track) < 2:
        raise ValueError("track needs at least two points")
    return any(
        _distance_to_segment(x, y, track[index], track[(index + 1) % len(track)]) <= width
        for index in range(len(track))
    )


def nearest_waypoint_index(
    x: float,
    y: float,
    *,
    track: Sequence[tuple[float, float]] = TRACK,
) -> int:

    if not track:
        raise ValueError("track cannot be empty")
    return min(
        range(len(track)),
        key=lambda index: (track[index][0] - x) ** 2 + (track[index][1] - y) ** 2,
    )


def next_waypoint(
    x: float,
    y: float,
    *,
    lookahead: int = 3,
    track: Sequence[tuple[float, float]] = TRACK,
) -> tuple[float, float]:

    if lookahead < 1:
        raise ValueError("lookahead must be positive")
    index = nearest_waypoint_index(x, y, track=track)
    return track[(index + lookahead) % len(track)]


def track_payload() -> list[list[float]]:

    return [[x, y] for x, y in TRACK]


class PhysicsEngine:

    def __init__(
        self,
        state: VehicleState,
        *,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.state = state
        self.tick_seconds = float(tick_seconds)
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running and not self._stop_event.is_set()

    def stop(self) -> None:

        self._stop_event.set()

    async def step(self, dt: float = TICK_SECONDS) -> dict[str, float | bool]:

        dt = float(clamp(float(dt), 0.0, 0.25))
        current = await self.state.snapshot()

        throttle = int(current["throttle"]) if current["ignition"] else 0
        brake = int(current["brake"])
        speed = float(current["speed"])
        steering = float(current["steering"])

        acceleration = (
            throttle / 255.0 * A_MAX
            - brake / 255.0 * B_MAX
            - DRAG * speed
        )
        speed = clamp(speed + acceleration * dt, 0.0, V_MAX)
        heading = float(current["heading"]) + (speed / WHEELBASE) * math.tan(steering) * dt
        heading = math.atan2(math.sin(heading), math.cos(heading))
        x = float(current["x"]) + speed * math.cos(heading) * dt
        y = float(current["y"]) + speed * math.sin(heading) * dt
        on_track = in_band(x, y)

        await self.state.apply_physics(
            x=x,
            y=y,
            heading=heading,
            speed=speed,
            on_track=on_track,
        )
        return {
            "x": x,
            "y": y,
            "heading": heading,
            "speed": speed,
            "on_track": on_track,
        }

    async def run(self, stop_event: asyncio.Event | None = None) -> None:

        self._running = True
        last_tick = time.monotonic()
        try:
            while not self._stop_event.is_set() and (
                stop_event is None or not stop_event.is_set()
            ):
                started = time.monotonic()
                dt = started - last_tick
                last_tick = started
                await self.step(dt)
                remaining = self.tick_seconds - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
        finally:
            self._running = False


Physics = PhysicsEngine
