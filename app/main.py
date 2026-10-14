from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn

from api import AccountRegistry, create_api
from autopilot_web import create_autopilot_app
from bus import CANBus
from cli import start_cli
from ecus.abs import ABSECU
from ecus.autopilot import AutopilotECU
from ecus.engine import EngineECU
from ecus.infotainment import InfotainmentECU
from ecus.telemetry import TelemetryECU
from physics import PhysicsEngine, track_payload
from state import VehicleState
from trust import CheckerTrust


@dataclass
class Runtime:

    state: VehicleState
    bus: CANBus
    trust: CheckerTrust
    engine: EngineECU
    abs: ABSECU
    autopilot: AutopilotECU
    telemetry: TelemetryECU
    infotainment: InfotainmentECU
    physics: PhysicsEngine
    stop_event: asyncio.Event
    track: list[list[float]]
    media_accounts: AccountRegistry = field(default_factory=AccountRegistry)
    blackbox_accounts: AccountRegistry = field(default_factory=AccountRegistry)


def build_runtime() -> Runtime:

    state = VehicleState()
    bus = CANBus()
    stop_event = asyncio.Event()
    store_root = Path(os.environ.get("STORE_ROOT", "/app/stores"))
    media_dir = Path(os.environ.get("INFOTAINMENT_STORE_DIR", str(store_root / "infotainment")))
    return Runtime(
        state=state,
        bus=bus,
        trust=CheckerTrust.from_environment(),
        engine=EngineECU(bus, state),
        abs=ABSECU(bus, state, storage_dir=store_root / "abs"),
        autopilot=AutopilotECU(bus, state),
        telemetry=TelemetryECU(bus, state, storage_dir=store_root / "telemetry"),
        infotainment=InfotainmentECU(
            media_dir,
            state,
            parser_path=os.environ.get("ID3PARSE_PATH", "/app/native/id3parse"),
        ),
        physics=PhysicsEngine(state),
        stop_event=stop_event,
        track=track_payload(),
        media_accounts=AccountRegistry(media_dir / "accounts.sqlite3"),
        blackbox_accounts=AccountRegistry(store_root / "telemetry" / "accounts.sqlite3"),
    )


def _can_drop_autopilot_uid() -> bool:

    return (
        os.name == "posix"
        and hasattr(os, "geteuid")
        and os.geteuid() == 0
        and os.environ.get("CARHACK_ISOLATE_AUTOPILOT", "1") != "0"
    )


def _spawn_autopilot_worker() -> subprocess.Popen[bytes] | None:

    if not _can_drop_autopilot_uid():
        return None
    try:
        import pwd

        account = pwd.getpwnam("ecu_autopilot")
    except (ImportError, KeyError):
        return None

    def drop_privileges() -> None:
        os.setgid(account.pw_gid)
        os.initgroups(account.pw_name, account.pw_gid)
        os.setuid(account.pw_uid)

    app_dir = Path(__file__).resolve().parent
    return subprocess.Popen(
        [sys.executable, "autopilot_web.py"],
        cwd=app_dir,
        preexec_fn=drop_privileges,
        close_fds=True,
    )


def _install_signals(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: stop_event.set())


async def _stop_tasks(tasks: list[asyncio.Task[Any]]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_service() -> None:
    runtime = build_runtime()
    _install_signals(runtime.stop_event)

    ecu_tasks = [
        asyncio.create_task(runtime.physics.run(runtime.stop_event), name="physics"),
        asyncio.create_task(runtime.engine.run(runtime.stop_event), name="engine"),
        asyncio.create_task(runtime.abs.run(runtime.stop_event), name="abs"),
        asyncio.create_task(runtime.autopilot.run(runtime.stop_event), name="autopilot"),
        asyncio.create_task(runtime.telemetry.run(runtime.stop_event), name="telemetry"),
        asyncio.create_task(runtime.infotainment.run(runtime.stop_event), name="infotainment"),
    ]
    await asyncio.sleep(0.05)
    cli_server = await start_cli(runtime)
    primary_server = uvicorn.Server(
        uvicorn.Config(create_api(runtime), host="0.0.0.0", port=8080, workers=1, log_level="warning", lifespan="off")
    )
    http_tasks = [asyncio.create_task(primary_server.serve(), name="main-http")]

    autopilot_process = _spawn_autopilot_worker()
    autopilot_server: uvicorn.Server | None = None
    if autopilot_process is None:
        autopilot_server = uvicorn.Server(
            uvicorn.Config(create_autopilot_app(trust=runtime.trust), host="0.0.0.0", port=8888, workers=1, log_level="warning", lifespan="off")
        )
        http_tasks.append(asyncio.create_task(autopilot_server.serve(), name="autopilot-http"))

    try:
        while not runtime.stop_event.is_set():
            if any(task.done() and task.exception() is not None for task in ecu_tasks + http_tasks):
                runtime.stop_event.set()
                break
            if autopilot_process is not None and autopilot_process.poll() is not None:
                runtime.stop_event.set()
                break
            try:
                await asyncio.wait_for(runtime.stop_event.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
    finally:
        runtime.stop_event.set()
        cli_server.close()
        await cli_server.wait_closed()
        primary_server.should_exit = True
        if autopilot_server is not None:
            autopilot_server.should_exit = True
        for ecu in (runtime.engine, runtime.abs, runtime.autopilot, runtime.telemetry, runtime.infotainment, runtime.physics):
            stop = getattr(ecu, "stop", None)
            if callable(stop):
                stop()
        await _stop_tasks(http_tasks)
        await _stop_tasks(ecu_tasks)
        if autopilot_process is not None and autopilot_process.poll() is None:
            autopilot_process.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(autopilot_process.wait), timeout=5)
            except asyncio.TimeoutError:
                autopilot_process.kill()
                await asyncio.to_thread(autopilot_process.wait)


if __name__ == "__main__":
    asyncio.run(run_service())
