from __future__ import annotations

import asyncio
import inspect
import io
import os
import secrets
import tarfile
from pathlib import Path
from typing import Any, Mapping

try:
    from ..storage import RecordStore
except ImportError:
    from storage import RecordStore


_MAX_MEDIA_BYTES = 4 * 1024 * 1024
_ID3_FRAME_NAMES = {"TIT2": "TITLE", "TPE1": "ARTIST", "COMM": "COMM"}


class MediaFormatError(ValueError):
    pass


async def _await_if_needed(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class InfotainmentECU:

    name = "infotainment"

    def __init__(
        self,
        storage_dir: str | Path,
        state: Any | None = None,
        *,
        parser_path: str | Path | None = None,
        retention_seconds: float | None = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.media_dir = self.storage_dir / "media"
        self.state = state
        self.parser_path = (
            Path(parser_path)
            if parser_path is not None
            else Path(__file__).resolve().parents[1] / "native" / "id3parse"
        )
        self._records = RecordStore(self.storage_dir / "records.sqlite3", lifetime=retention_seconds)
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._healthy = False

    async def ping(self) -> bool:
        return self._healthy and not self._stop_event.is_set()

    def stop(self) -> None:

        self._stop_event.set()

    async def upload_media(
        self, content: bytes, owner: str
    ) -> tuple[str, dict[str, str]]:

        if not isinstance(content, bytes) or not content:
            raise MediaFormatError("media upload must contain bytes")
        if len(content) > _MAX_MEDIA_BYTES:
            raise MediaFormatError("media upload is too large")
        if not isinstance(owner, str) or not owner:
            raise MediaFormatError("media owner is required")

        await asyncio.to_thread(self._ensure_storage)
        track_id = secrets.token_urlsafe(18)
        media_path = self.media_dir / f"{track_id}.mp3"
        flag_path = self.storage_dir / f"{track_id}.flag"
        await asyncio.to_thread(self._write_private, media_path, content)
        try:
            metadata = await self._read_metadata(media_path, content)
            flag = metadata.get("COMM")
            if not flag:
                raise MediaFormatError("ID3 COMM frame is required")
            await asyncio.to_thread(self._write_private, flag_path, flag.encode("utf-8"))
        except Exception:
            await asyncio.to_thread(self._unlink_if_exists, media_path)
            await asyncio.to_thread(self._unlink_if_exists, flag_path)
            raise

        async with self._lock:
            self._records.put("media", track_id, owner, b"")
            expired = self._records.prune("media")
        for old in expired:
            await asyncio.to_thread(self._unlink_if_exists, self.storage_dir / f"{old.key}.flag")
            await asyncio.to_thread(self._unlink_if_exists, self.media_dir / f"{old.key}.mp3")

        await self._log("accepted media upload")
        public_metadata = {key: value for key, value in metadata.items() if key != "COMM"}
        return track_id, public_metadata

    async def get_media_flag(self, track_id: str, owner: str) -> str | None:

        async with self._lock:
            record = self._records.get("media", track_id, owner)
        if record is None:
            return None
        try:
            return await asyncio.to_thread((self.storage_dir / f"{record.key}.flag").read_text, "utf-8")
        except FileNotFoundError:
            return None

    async def evaluate_album_art(
        self, image_bytes: bytes, values: Mapping[str, Any]
    ) -> dict[str, object]:

        return await asyncio.to_thread(
            self._evaluate_album_art_sync, image_bytes, dict(values)
        )

    async def extract_media_pack(self, archive_bytes: bytes) -> list[str]:

        if not isinstance(archive_bytes, bytes):
            raise MediaFormatError("media pack must contain bytes")
        await asyncio.to_thread(self._ensure_storage)
        destination = self.media_dir / "packs" / secrets.token_urlsafe(12)
        await asyncio.to_thread(extract_media_pack, archive_bytes, destination)
        return await asyncio.to_thread(self._pack_listing, destination)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        await asyncio.to_thread(self._ensure_storage)
        await self._set_status("ok")
        self._healthy = True
        try:
            if stop_event is None:
                await self._stop_event.wait()
            else:
                stop_task = asyncio.create_task(stop_event.wait())
                local_task = asyncio.create_task(self._stop_event.wait())
                done, pending = await asyncio.wait(
                    {stop_task, local_task}, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._healthy = False
            await self._set_status("down")
            raise
        finally:
            self._healthy = False

    async def _read_metadata(self, media_path: Path, content: bytes) -> dict[str, str]:
        if self.parser_path.is_file():
            process = await asyncio.create_subprocess_exec(
                str(self.parser_path),
                str(media_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=2.0)
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.wait()
                raise MediaFormatError("metadata parser timed out") from exc
            if process.returncode != 0:
                raise MediaFormatError("metadata parser rejected the upload")
            metadata = self._parse_native_output(stdout)
        else:
            metadata = self._parse_id3v2(content)
        if not metadata:
            raise MediaFormatError("media file has no supported ID3 metadata")
        return metadata

    @staticmethod
    def _parse_native_output(stdout: bytes) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            key, separator, value = raw_line.partition("=")
            if separator and key in {"TITLE", "ARTIST", "COMM"}:
                metadata[key] = value
        return metadata

    @classmethod
    def _parse_id3v2(cls, content: bytes) -> dict[str, str]:
        if len(content) < 10 or content[:3] != b"ID3":
            raise MediaFormatError("ID3v2 header is required")
        version = content[3]
        if version not in (3, 4):
            raise MediaFormatError("only ID3v2.3/v2.4 media is supported")
        tag_size = cls._synchsafe(content[6:10])
        tag_end = 10 + tag_size
        if tag_end > len(content):
            raise MediaFormatError("truncated ID3 tag")

        metadata: dict[str, str] = {}
        cursor = 10
        while cursor + 10 <= tag_end:
            frame_id_bytes = content[cursor : cursor + 4]
            if frame_id_bytes == b"\0\0\0\0":
                break
            try:
                frame_id = frame_id_bytes.decode("ascii")
            except UnicodeDecodeError:
                raise MediaFormatError("invalid ID3 frame identifier") from None
            size_bytes = content[cursor + 4 : cursor + 8]
            frame_size = (
                cls._synchsafe(size_bytes) if version == 4 else int.from_bytes(size_bytes, "big")
            )
            frame_start = cursor + 10
            frame_end = frame_start + frame_size
            if frame_size < 0 or frame_end > tag_end:
                raise MediaFormatError("truncated ID3 frame")
            name = _ID3_FRAME_NAMES.get(frame_id)
            if name:
                value = cls._decode_frame(frame_id, content[frame_start:frame_end])
                if value:
                    metadata[name] = value
            cursor = frame_end
        return metadata

    @staticmethod
    def _synchsafe(raw: bytes) -> int:
        if len(raw) != 4 or any(byte & 0x80 for byte in raw):
            raise MediaFormatError("invalid synchsafe ID3 length")
        return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]

    @staticmethod
    def _decode_frame(frame_id: str, payload: bytes) -> str:
        if not payload:
            return ""
        encoding = payload[0]
        if frame_id == "COMM":
            if len(payload) < 5:
                return ""
            text_start = InfotainmentECU._comment_text_start(payload, encoding)
            raw_text = payload[text_start:]
        else:
            raw_text = payload[1:]
        codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(encoding)
        if codec is None:
            return ""
        return raw_text.decode(codec, errors="replace").rstrip("\0")

    @staticmethod
    def _comment_text_start(payload: bytes, encoding: int) -> int:
        offset = 4
        if encoding in (1, 2):
            while offset + 1 < len(payload):
                if payload[offset : offset + 2] == b"\0\0":
                    return offset + 2
                offset += 2
            return len(payload)
        marker = payload.find(b"\0", offset)
        return len(payload) if marker < 0 else marker + 1

    @staticmethod
    def _ensure_storage_for(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    def _ensure_storage(self) -> None:
        self._ensure_storage_for(self.storage_dir)
        self._ensure_storage_for(self.media_dir)

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _unlink_if_exists(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _evaluate_album_art_sync(
        image_bytes: bytes, values: dict[str, Any]
    ) -> dict[str, object]:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as opened:
            source = opened.copy()
        expression = values.pop("expression", "a")
        if not isinstance(expression, str):
            raise MediaFormatError("album-art expression must be text")
        values.setdefault("a", source)
        result = evaluate_album_art(expression, values)
        return {
            "status": "ok",
            "mode": str(getattr(result, "mode", "unknown")),
            "size": list(getattr(result, "size", source.size)),
        }

    @staticmethod
    def _pack_listing(destination: Path) -> list[str]:
        if not destination.exists():
            return []
        return sorted(
            str(path.relative_to(destination))
            for path in destination.rglob("*")
            if path.is_file()
        )[:128]

    async def _set_status(self, status: str) -> None:
        if self.state is None:
            return
        setter = getattr(self.state, "set_ecu_status", None)
        if setter is not None:
            await _await_if_needed(setter("infotainment", status))
            return
        ecus = getattr(self.state, "ecus", None)
        if isinstance(ecus, dict):
            ecus["infotainment"] = status

    async def _log(self, message: str) -> None:
        if self.state is None:
            return
        logger = getattr(self.state, "append_ecu_log", None)
        if logger is not None:
            await _await_if_needed(logger("infotainment", message))
            return
        logs = getattr(self.state, "ecu_logs", None)
        if isinstance(logs, dict):
            logs.setdefault("infotainment", []).append(message)
            del logs["infotainment"][:-20]


def evaluate_album_art(expression: str, environment: Mapping[str, Any]) -> Any:

    from PIL import ImageMath

    return ImageMath.eval(expression, **dict(environment))


def extract_media_pack(archive_bytes: bytes, destination: str | Path) -> None:

    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
        archive.extractall(target)
