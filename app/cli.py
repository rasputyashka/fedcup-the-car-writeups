from __future__ import annotations

import asyncio
import inspect
import secrets
from collections.abc import Awaitable
from typing import Any

from state import CAN_ID_MAX, DIAGNOSTIC_ID_MIN


MAX_LINE = 4096


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _hex(value: str, *, limit: int | None = None) -> int:
    try:
        result = int(value.removeprefix("0x"), 16)
    except ValueError as exc:
        raise ValueError("expected hexadecimal value") from exc
    if result < 0 or (limit is not None and result > limit):
        raise ValueError("hexadecimal value outside range")
    return result


def _payload(value: str) -> bytes:
    if len(value) % 2:
        raise ValueError("hex data must have even length")
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("invalid hex data") from exc
    if len(data) > 8:
        raise ValueError("CAN data must be at most 8 bytes")
    return data


def _blob(value: str) -> bytes:

    if len(value) % 2:
        raise ValueError("hex data must have even length")
    try:
        data = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("invalid hex data") from exc
    if not data or len(data) > 1024:
        raise ValueError("diagnostic write outside range")
    return data


async def _bus_recent(
    bus: Any, can_id: int, count: int, diag_ok: bool
) -> list[Any]:
    recent = getattr(bus, "recent")
    try:
        return list(await _maybe_await(recent(can_id, count, diag_ok=diag_ok)))
    except TypeError:
        return list(await _maybe_await(recent(can_id, count)))


async def _bus_sniff(bus: Any, count: int, diag_ok: bool) -> list[Any]:
    sniff = getattr(bus, "sniff", None)
    if sniff is not None:
        try:
            return list(
                await _maybe_await(sniff(count=count, diag_ok=diag_ok))
            )
        except TypeError:
            return list(await _maybe_await(sniff(count)))
    ring = getattr(bus, "ring", ())
    return list(ring)[-count:]


def _frame_parts(frame: Any) -> tuple[int, bytes, float]:
    if hasattr(frame, "id"):
        return int(frame.id), bytes(frame.data), float(frame.ts)
    identifier, data, timestamp = frame
    return int(identifier), bytes(data), float(timestamp)


class DiagnosticCLI:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session_id = secrets.token_urlsafe(18)
        diag_ok = False
        pending_challenge: str | None = None
        writer.write(b"CarHack diagnostic ready\n")
        await writer.drain()
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_LINE:
                    await self._write(writer, "ERR line too long")
                    break
                try:
                    command = line.decode("ascii").strip().split()
                except UnicodeDecodeError:
                    await self._write(writer, "ERR ascii only")
                    continue
                if not command:
                    continue
                try:
                    answer, diag_ok, pending_challenge = await self._dispatch(
                        command, diag_ok, pending_challenge, session_id
                    )
                except ValueError as exc:
                    answer = f"ERR {exc}"
                except PermissionError:
                    answer = "ERR unauthorized"
                except LookupError:
                    answer = "ERR not found"
                except Exception:
                    answer = "ERR internal"
                await self._write(writer, answer)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _write(self, writer: asyncio.StreamWriter, value: str) -> None:
        writer.write(value.encode("ascii", "replace") + b"\n")
        await writer.drain()

    async def _dispatch(
        self,
        command: list[str],
        diag_ok: bool,
        pending_challenge: str | None,
        session_id: str,
    ) -> tuple[str, bool, str | None]:
        name, *args = command
        if name == "help":
            return (
                "OK help ping challenge diag_auth diag_store diag_fetch can_send can_recv can_sniff store_abs write_abs read_abs",
                diag_ok,
                pending_challenge,
            )
        if name == "ping" and len(args) == 1:
            ecu = getattr(self.runtime, args[0], None)
            if ecu is None or not bool(await _maybe_await(ecu.ping())):
                raise LookupError
            return (f"PONG {args[0]}", diag_ok, pending_challenge)
        if name == "challenge" and not args:
            issued = self.runtime.trust.issue("diag", session_id)
            return (f"CHALLENGE {issued.value}", diag_ok, issued.value)
        if name == "diag_auth" and len(args) == 1:
            if pending_challenge is None:
                raise PermissionError
            accepted = self.runtime.trust.verify_and_consume(
                pending_challenge, args[0], scope="diag", subject=session_id
            )
            if not accepted:
                raise PermissionError
            return ("OK diag", True, None)
        if name == "diag_store" and len(args) == 1:
            if not diag_ok:
                raise PermissionError
            try:
                flag = bytes.fromhex(args[0]).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("invalid diagnostic flag") from exc
            info = await _maybe_await(
                self.runtime.telemetry.store_diagnostic_flag(flag)
            )
            return (
                f"DIAG {info.base_seq:02x} {info.chunk_count:x} {info.record_id}",
                diag_ok,
                pending_challenge,
            )
        if name == "diag_fetch" and len(args) == 1:
            if not diag_ok:
                raise PermissionError
            frames = await self.runtime.telemetry.get_diagnostic_frames(
                args[0]
            )
            if frames is None:
                raise LookupError
            lines = [f"FRAME 7DF {frame.hex()}" for frame in frames]
            return ("\n".join(lines + ["END"]), diag_ok, pending_challenge)
        if name == "can_send" and len(args) == 2:
            identifier, payload = (
                _hex(args[0], limit=CAN_ID_MAX),
                _payload(args[1]),
            )
            await _maybe_await(self.runtime.bus.publish(identifier, payload))
            return ("OK", diag_ok, pending_challenge)
        if name == "can_recv" and len(args) in (1, 2):
            identifier = _hex(args[0], limit=CAN_ID_MAX)
            if identifier >= DIAGNOSTIC_ID_MIN and not diag_ok:
                raise PermissionError
            count = _hex(args[1], limit=0x400) if len(args) == 2 else 1
            lines = []
            for frame in await _bus_recent(
                self.runtime.bus, identifier, count, diag_ok
            ):
                can_id, data, _ts = _frame_parts(frame)
                lines.append(f"FRAME {can_id:03X} {data.hex()}")
            return (
                "\n".join(lines + ["END"]) if lines else "EMPTY",
                diag_ok,
                pending_challenge,
            )
        if name == "can_sniff" and len(args) <= 1:
            seconds = float(args[0]) if args else 0.0
            if not 0 <= seconds <= 10:
                raise ValueError("seconds outside range")
            if seconds:
                await asyncio.sleep(seconds)
            lines = []
            for frame in await _bus_sniff(self.runtime.bus, 128, diag_ok):
                can_id, data, _ts = _frame_parts(frame)
                lines.append(f"FRAME {can_id:03X} {data.hex()}")
            return (
                "\n".join(lines + ["END"]) if lines else "EMPTY",
                diag_ok,
                pending_challenge,
            )
        if name == "store_abs" and len(args) == 1:
            address, token = await _maybe_await(
                self.runtime.abs.store_abs(_blob(args[0]))
            )
            return (f"ABS {address:x} {token}", diag_ok, pending_challenge)
        if name == "write_abs" and len(args) in (2, 3):
            address, data = _hex(args[0], limit=0x3FFF), _blob(args[1])
            requested = args[2] if len(args) == 3 else None
            token = await _maybe_await(
                self.runtime.abs.write_abs(address, data, requested)
            )
            return (f"WTOK {token}", diag_ok, pending_challenge)
        if name == "read_abs" and len(args) in (2, 3):
            address = _hex(args[0], limit=0x3FFF)
            length = _hex(args[1], limit=0x4000)
            token = args[2] if len(args) == 3 else None
            data = await _maybe_await(
                self.runtime.abs.read_abs(address, length, token)
            )
            if data is None:
                raise PermissionError
            return (f"DATA {bytes(data).hex()}", diag_ok, pending_challenge)
        raise ValueError("unknown command")


async def start_cli(
    runtime: Any, host: str = "0.0.0.0", port: int = 31337
) -> asyncio.AbstractServer:

    dispatcher = DiagnosticCLI(runtime)
    return await asyncio.start_server(
        dispatcher.serve, host, port, limit=MAX_LINE
    )
