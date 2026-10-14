from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import inspect
import secrets
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

try:
    from .storage import RecordStore
except ImportError:
    from storage import RecordStore


MAX_UPLOAD_BYTES = 1_048_576


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class AccountRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self._records = RecordStore(path)
        self._lock = asyncio.Lock()

    @staticmethod
    def _validate(username: object, password: object) -> tuple[str, str]:
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("credentials must be strings")
        if not (3 <= len(username) <= 96 and 12 <= len(password) <= 256):
            raise ValueError("invalid credential length")
        return username, password

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**12, r=8, p=1
        )

    async def register(self, username: object, password: object) -> None:
        username, password = self._validate(username, password)
        async with self._lock:
            salt = secrets.token_bytes(16)
            if not self._records.put(
                "accounts",
                username,
                username,
                salt + self._derive(password, salt),
                permanent=True,
                create_only=True,
            ):
                raise ValueError("account already exists")

    async def authenticate(
        self, username: object, password: object
    ) -> str | None:
        try:
            username, password = self._validate(username, password)
        except ValueError:
            return None
        async with self._lock:
            entry = self._records.get("accounts", username)
        if entry is None:
            return None
        salt, expected = entry.data[:16], entry.data[16:]
        supplied = self._derive(password, salt)
        return username if hmac.compare_digest(expected, supplied) else None


def _json_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _decode_b64(value: object, *, label: str) -> bytes:
    if not isinstance(value, str):
        raise _json_error(422, f"{label} must be base64 text")
    try:
        result = base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as exc:
        raise _json_error(422, f"invalid {label}") from exc
    if len(result) > MAX_UPLOAD_BYTES:
        raise _json_error(413, f"{label} is too large")
    return result


async def _snapshot(runtime: Any) -> dict[str, Any]:
    state = runtime.state
    snapshot = await _maybe_await(getattr(state, "snapshot", state.to_dict)())
    snapshot["track"] = getattr(runtime, "track", [])
    return snapshot


async def _ping(ecu: Any) -> bool:
    try:
        return bool(await _maybe_await(ecu.ping()))
    except Exception:
        return False


def _registry(runtime: Any, name: str) -> AccountRegistry:
    value = getattr(runtime, name, None)
    if value is None:
        value = AccountRegistry()
        setattr(runtime, name, value)
    return value


def create_api(runtime: Any) -> FastAPI:

    app = FastAPI(title="the_car", docs_url=None, redoc_url=None)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        ecus = (
            runtime.engine,
            runtime.abs,
            runtime.autopilot,
            runtime.telemetry,
            runtime.infotainment,
        )
        if not all(await asyncio.gather(*(_ping(ecu) for ecu in ecus))):
            raise _json_error(503, "ecu unavailable")
        return {"status": "ok"}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return await _snapshot(runtime)

    @app.get("/api/track")
    async def track() -> dict[str, object]:
        return {"points": getattr(runtime, "track", [])}

    @app.websocket("/ws/state")
    async def state_ws(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while not getattr(runtime, "stop_event").is_set():
                await ws.send_json(await _snapshot(runtime))
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            return

    @app.post("/api/media/register")
    async def media_register(payload: dict[str, Any]) -> dict[str, str]:
        try:
            await _registry(runtime, "media_accounts").register(
                payload.get("u"), payload.get("p")
            )
        except ValueError as exc:
            raise _json_error(409, str(exc)) from exc
        return {"status": "ok"}

    @app.post("/api/media/upload")
    async def media_upload(payload: dict[str, Any]) -> dict[str, str]:
        owner = await _registry(runtime, "media_accounts").authenticate(
            payload.get("u"), payload.get("p")
        )
        if owner is None:
            raise _json_error(401, "invalid credentials")
        mp3 = _decode_b64(payload.get("mp3_b64"), label="mp3_b64")
        try:
            track_id, _metadata = await runtime.infotainment.upload_media(
                mp3, owner
            )
        except Exception as exc:
            raise _json_error(422, "media rejected") from exc
        return {"track_id": track_id}

    @app.get("/api/media/flag/{track_id}")
    async def media_flag(
        track_id: str, u: str = "", p: str = ""
    ) -> PlainTextResponse:
        owner = await _registry(runtime, "media_accounts").authenticate(u, p)
        if owner is None:
            raise _json_error(401, "invalid credentials")
        flag = await runtime.infotainment.get_media_flag(track_id, owner)
        if flag is None:
            raise _json_error(404, "track not found")
        return PlainTextResponse(flag)

    @app.post("/api/media/albumart")
    async def album_art(payload: dict[str, Any]) -> dict[str, object]:
        image = _decode_b64(payload.get("image_b64"), label="image_b64")
        values = payload.get("values", {})
        try:
            return await _maybe_await(
                runtime.infotainment.evaluate_album_art(image, values)
            )
        except AttributeError as exc:
            raise _json_error(501, "album-art handler unavailable") from exc
        except Exception as exc:
            raise _json_error(422, "album-art rejected") from exc

    @app.post("/api/media/pack")
    async def media_pack(payload: dict[str, Any]) -> dict[str, object]:
        archive = _decode_b64(payload.get("tar_b64"), label="tar_b64")
        try:
            extracted = await _maybe_await(
                runtime.infotainment.extract_media_pack(archive)
            )
        except AttributeError as exc:
            raise _json_error(501, "media-pack handler unavailable") from exc
        except Exception as exc:
            raise _json_error(422, "media pack rejected") from exc
        return {"files": extracted}

    @app.post("/api/blackbox/register")
    async def blackbox_register(payload: dict[str, Any]) -> dict[str, str]:
        try:
            await _registry(runtime, "blackbox_accounts").register(
                payload.get("u"), payload.get("p")
            )
        except ValueError as exc:
            raise _json_error(409, str(exc)) from exc
        return {"status": "ok"}

    @app.post("/api/blackbox/store")
    async def blackbox_store(payload: dict[str, Any]) -> dict[str, str]:
        owner = await _registry(runtime, "blackbox_accounts").authenticate(
            payload.get("u"), payload.get("p")
        )
        flag = payload.get("flag")
        if owner is None:
            raise _json_error(401, "invalid credentials")
        if not isinstance(flag, str) or not flag:
            raise _json_error(422, "flag is required")
        record_id = await runtime.telemetry.store_blackbox(flag, owner)
        return {"record_id": record_id}

    @app.get("/api/blackbox/get/{record_id}")
    async def blackbox_get(
        record_id: str, u: str = "", p: str = ""
    ) -> PlainTextResponse:
        owner = await _registry(runtime, "blackbox_accounts").authenticate(
            u, p
        )
        if owner is None:
            raise _json_error(401, "invalid credentials")
        flag = await runtime.telemetry.get_blackbox(record_id, owner)
        if flag is None:
            raise _json_error(404, "record not found")
        return PlainTextResponse(flag)

    @app.get("/api/blackbox/raw/{record_id}")
    async def blackbox_raw(record_id: str) -> dict[str, str]:
        ciphertext = await runtime.telemetry.get_blackbox_raw(record_id)
        if ciphertext is None:
            raise _json_error(404, "record not found")
        return {"ct_b64": ciphertext}

    return app
