from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jwt
import yaml
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

try:
    from .trust import CheckerTrust
    from .storage import retention_seconds
except ImportError:
    from trust import CheckerTrust
    from storage import retention_seconds


JWT_ISSUER = "carhack-autopilot"
SERVICE_TOKEN_ALGORITHM = "HS256"
SERVICE_SECRET_LABEL = b"carhack-autopilot/service-token/v1"
JWT_LIFETIME_SECONDS = 15 * 60
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ROUTE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")


class StorageUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    username: str
    role: str


class CredentialsRequest(BaseModel):
    u: str = Field(min_length=1, max_length=64)
    p: str = Field(min_length=1, max_length=256)


class RouteSaveRequest(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    secret: str = Field(min_length=1, max_length=4096)


class RouteExportRequest(BaseModel):
    key: str = Field(min_length=1, max_length=96)


class CheckerChallengeRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=64)


class CheckerProvisionRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    challenge: str = Field(min_length=1, max_length=1024)
    signature: str = Field(min_length=1, max_length=256)


class AdminSecretRequest(BaseModel):
    flag: str = Field(min_length=1, max_length=4096)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _normalise_pem(value: str) -> bytes:
    return value.replace("\\n", "\n").encode("utf-8")


def _valid_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))


def _valid_route_key(key: str) -> bool:
    return bool(ROUTE_KEY_RE.fullmatch(key))


class AutopilotStore:
    def __init__(
        self, root: str | Path, *, lifetime: float | None = None
    ) -> None:
        self.retention_seconds = retention_seconds(lifetime)
        self.root = Path(root)
        self.accounts_path = self.root / "accounts.json"
        self.route_store = self.root / "route_store"
        self.route_meta = self.root / "route_meta"
        self.exports = self.root / "exports"
        self.admin_store = self.root / "admin_store"
        self._lock = threading.RLock()

        try:
            for directory in (
                self.root,
                self.route_store,
                self.route_meta,
                self.exports,
                self.admin_store,
            ):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(directory, 0o700)
        except OSError as exc:
            raise StorageUnavailable from exc

    def create_account(self, username: str, password: str) -> bool:
        if not _valid_username(username):
            return False
        salt = os.urandom(16)
        digest = self._password_digest(password, salt)
        with self._lock:
            accounts = self._accounts()
            if username in accounts:
                return False
            accounts[username] = {
                "salt": _b64url(salt),
                "digest": _b64url(digest),
                "admin": False,
                "created": time.time(),
            }
            self._write_json(self.accounts_path, accounts)
        return True

    def authenticate(self, username: str, password: str) -> Optional[Session]:
        if not _valid_username(username):
            return None
        with self._lock:
            account = self._accounts().get(username)
        if not isinstance(account, dict):
            return None
        try:
            salt = self._decode_b64_field(account.get("salt"))
            expected = self._decode_b64_field(account.get("digest"))
        except StorageUnavailable:
            raise
        if not hmac.compare_digest(
            expected, self._password_digest(password, salt)
        ):
            return None
        return Session(
            username, "admin" if account.get("admin") is True else "user"
        )

    def account_exists(self, username: str) -> bool:
        if not _valid_username(username):
            return False
        with self._lock:
            return username in self._accounts()

    def provision_admin(self, username: str) -> bool:
        if not _valid_username(username):
            return False
        with self._lock:
            accounts = self._accounts()
            account = accounts.get(username)
            if not isinstance(account, dict):
                return False
            account["admin"] = True
            accounts[username] = account
            self._write_json(self.accounts_path, accounts)
        return True

    def save_route(self, username: str, key: str, secret: str) -> bool:
        if not _valid_route_key(key):
            return False
        with self._lock:
            meta = self._read_json(self.route_meta / f"{key}.json")
            if (
                isinstance(meta, dict)
                and meta.get("owner") != username
                and meta.get("created", 0)
                > time.time() - self.retention_seconds
            ):
                return False
            self._write_text(self.route_store / key, secret)
            self._write_json(
                self.route_meta / f"{key}.json",
                {"owner": username, "created": time.time()},
            )
            self._prune_routes()
        return True

    def load_route(self, username: str, key: str) -> Optional[str]:
        if not _valid_route_key(key):
            return None
        with self._lock:
            meta = self._read_json(self.route_meta / f"{key}.json")
            if (
                not isinstance(meta, dict)
                or meta.get("owner") != username
                or meta.get("created", 0)
                <= time.time() - self.retention_seconds
            ):
                return None
            return self._read_text(self.route_store / key)

    def read_export(self, requested_path: str) -> Optional[str]:

        if (
            not requested_path
            or "\x00" in requested_path
            or len(requested_path) > 512
        ):
            return None
        try:
            candidate = self.exports / requested_path
            return self._read_text(candidate)
        except (OSError, ValueError):
            return None

    def write_export(self, username: str, key: str) -> Optional[str]:
        if not _valid_route_key(key):
            return None
        with self._lock:
            meta = self._read_json(self.route_meta / f"{key}.json")
            if not isinstance(meta, dict) or meta.get("owner") != username:
                return None
            secret = self._read_text(self.route_store / key)
            if secret is None:
                return None
            name = f"{key}.txt"
            self._write_text(
                self.exports / name,
                "key=%s\nbytes=%d\n" % (key, len(secret.encode("utf-8"))),
            )
            self._prune_plain_records(self.exports)
        return name

    def save_admin_secret(self, username: str, flag: str) -> None:
        with self._lock:
            self._write_text(self.admin_store / username, flag)
            self._prune_plain_records(self.admin_store)

    def load_admin_secret(self, username: str) -> Optional[str]:
        if not _valid_username(username):
            return None
        with self._lock:
            path = self.admin_store / username
            if (
                not path.exists()
                or path.stat().st_mtime <= time.time() - self.retention_seconds
            ):
                return None
            return self._read_text(path)

    def _accounts(self) -> dict[str, Any]:
        data = self._read_json(self.accounts_path, default={})
        if not isinstance(data, dict):
            raise StorageUnavailable
        return data

    @staticmethod
    def _password_digest(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )

    @staticmethod
    def _decode_b64_field(value: object) -> bytes:
        if not isinstance(value, str):
            raise StorageUnavailable
        try:
            padded = value + "=" * (-len(value) % 4)
            return base64.urlsafe_b64decode(padded.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise StorageUnavailable from exc

    @staticmethod
    def _read_text(path: Path) -> Optional[str]:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageUnavailable from exc

    @staticmethod
    def _read_json(path: Path, default: Any = None) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return default
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageUnavailable from exc

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        AutopilotStore._atomic_write(path, value.encode("utf-8"))

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        serialized = json.dumps(
            value, separators=(",", ":"), ensure_ascii=False
        )
        AutopilotStore._atomic_write(path, serialized.encode("utf-8"))

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise StorageUnavailable from exc

    def _prune_routes(self) -> None:
        records = sorted(
            self.route_meta.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for meta in records:
            if meta.stat().st_mtime > time.time() - self.retention_seconds:
                continue
            key = meta.stem
            try:
                meta.unlink(missing_ok=True)
                (self.route_store / key).unlink(missing_ok=True)
            except OSError as exc:
                raise StorageUnavailable from exc

    def _prune_plain_records(self, directory: Path) -> None:
        records = sorted(
            (path for path in directory.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for record in records:
            if record.stat().st_mtime > time.time() - self.retention_seconds:
                continue
            try:
                record.unlink(missing_ok=True)
            except OSError as exc:
                raise StorageUnavailable from exc


class JwtKeyMaterial:
    def __init__(
        self,
        private_pem: Optional[str] = None,
        public_pem: Optional[str] = None,
    ) -> None:
        configured_private = private_pem or os.environ.get("JWT_RSA_PRIV")
        configured_public = public_pem or os.environ.get("JWT_RSA_PUB")

        if configured_private:
            try:
                private_key = serialization.load_pem_private_key(
                    _normalise_pem(configured_private), password=None
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "JWT_RSA_PRIV is not a valid private key"
                ) from exc
            if not isinstance(private_key, rsa.RSAPrivateKey):
                raise ValueError("JWT_RSA_PRIV must be an RSA private key")
            if configured_public:
                try:
                    supplied_public = serialization.load_pem_public_key(
                        _normalise_pem(configured_public)
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "JWT_RSA_PUB is not a valid public key"
                    ) from exc
                if not isinstance(supplied_public, rsa.RSAPublicKey):
                    raise ValueError("JWT_RSA_PUB must be an RSA public key")
                if (
                    supplied_public.public_numbers()
                    != private_key.public_key().public_numbers()
                ):
                    raise ValueError(
                        "JWT_RSA_PRIV and JWT_RSA_PUB do not match"
                    )
        elif configured_public:
            raise ValueError(
                "JWT_RSA_PRIV is required when JWT_RSA_PUB is configured"
            )
        else:
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )

        public_key = private_key.public_key()
        self._private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._public_pem = public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._public_der = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._public_key = public_key
        digest = hashes.Hash(hashes.SHA256())
        digest.update(self._public_der)
        self.kid = _b64url(digest.finalize()[:12])
        self._service_secret = hashlib.sha256(
            SERVICE_SECRET_LABEL + self._public_der
        ).digest()

    def issue(self, session: Session) -> str:
        now = int(time.time())
        claims = {
            "iss": JWT_ISSUER,
            "sub": session.username,
            "role": session.role,
            "iat": now,
            "exp": now + JWT_LIFETIME_SECONDS,
            "jti": secrets.token_urlsafe(16),
        }
        token = jwt.encode(
            claims,
            self._private_pem,
            algorithm="RS256",
            headers={"kid": self.kid},
        )
        return token.decode("ascii") if isinstance(token, bytes) else token

    def decode(self, token: str) -> Session:

        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm == SERVICE_TOKEN_ALGORITHM:
                claims = jwt.decode(
                    token,
                    self._service_secret,
                    algorithms=[SERVICE_TOKEN_ALGORITHM],
                    issuer=JWT_ISSUER,
                    options={"require": ["exp", "sub", "role", "iss"]},
                )
            else:
                claims = jwt.decode(
                    token,
                    self._public_pem,
                    algorithms=["RS256"],
                    issuer=JWT_ISSUER,
                    options={"require": ["exp", "sub", "role", "iss"]},
                )
        except (jwt.PyJWTError, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid session",
                headers={"WWW-Authenticate": "Bearer"},
            ) from None
        username = claims.get("sub")
        role = claims.get("role")
        if (
            not isinstance(username, str)
            or not _valid_username(username)
            or role not in {"user", "admin"}
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid session",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return Session(username, role)

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        numbers = self._public_key.public_numbers()
        width = (self._public_key.key_size + 7) // 8
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": _b64url(numbers.n.to_bytes(width, "big")),
                    "e": _b64url(
                        numbers.e.to_bytes(
                            (numbers.e.bit_length() + 7) // 8, "big"
                        )
                    ),
                }
            ]
        }


def create_autopilot_app(
    *,
    trust: Optional[CheckerTrust] = None,
    storage_root: Optional[str | Path] = None,
    jwt_private_key: Optional[str] = None,
    jwt_public_key: Optional[str] = None,
) -> FastAPI:

    app = FastAPI(title="the_car Autopilot", docs_url=None, redoc_url=None)
    app.state.trust = trust or CheckerTrust.from_environment()
    app.state.store = AutopilotStore(
        storage_root
        or os.environ.get("AUTOPILOT_STORE_DIR", "/app/stores/autopilot")
    )
    app.state.jwt_keys = JwtKeyMaterial(jwt_private_key, jwt_public_key)

    def current_session(request: Request) -> Session:
        authorization = request.headers.get("authorization")
        token: Optional[str]
        if authorization:
            scheme, separator, supplied_token = authorization.partition(" ")
            if (
                scheme.lower() != "bearer"
                or not separator
                or not supplied_token
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="authentication required",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            token = supplied_token
        else:
            token = request.cookies.get("session")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return app.state.jwt_keys.decode(token)

    def admin_session(session: Session = Depends(current_session)) -> Session:
        if session.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="administrator required",
            )
        return session

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "autopilot"}

    @app.get("/api/jwks")
    def jwks() -> dict[str, list[dict[str, str]]]:
        return app.state.jwt_keys.jwks()

    @app.post("/register", status_code=status.HTTP_201_CREATED)
    def register(payload: CredentialsRequest) -> dict[str, bool]:
        try:
            created = app.state.store.create_account(payload.u, payload.p)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if not created:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="registration unavailable",
            )
        return {"registered": True}

    @app.post("/login")
    def login(
        payload: CredentialsRequest, response: Response
    ) -> dict[str, str]:
        try:
            session = app.state.store.authenticate(payload.u, payload.p)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            )
        token = app.state.jwt_keys.issue(session)
        response.set_cookie(
            "session",
            token,
            httponly=True,
            samesite="lax",
            max_age=JWT_LIFETIME_SECONDS,
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "role": session.role,
        }

    @app.post("/api/route/save")
    def save_route(
        payload: RouteSaveRequest, session: Session = Depends(current_session)
    ) -> dict[str, str]:
        try:
            saved = app.state.store.save_route(
                session.username, payload.key, payload.secret
            )
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if not saved:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid route key",
            )
        return {"key": payload.key}

    @app.get("/api/route/load")
    def load_route(
        key: str, session: Session = Depends(current_session)
    ) -> dict[str, str]:
        try:
            value = app.state.store.load_route(session.username, key)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="route not found"
            )
        return {"key": key, "secret": value}

    @app.post("/api/route/export")
    def create_export(
        payload: RouteExportRequest,
        session: Session = Depends(current_session),
    ) -> dict[str, str]:
        try:
            name = app.state.store.write_export(session.username, payload.key)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if name is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="route not found"
            )
        return {"name": name}

    @app.get("/api/route/export")
    def export_route(path: str) -> PlainTextResponse:
        try:
            value = app.state.store.read_export(path)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="export not found",
            )
        return PlainTextResponse(value, media_type="text/plain")

    @app.post("/api/checker/challenge")
    def checker_challenge(
        payload: CheckerChallengeRequest,
    ) -> dict[str, int | str]:
        if payload.action != "admin_provision" or not _valid_username(
            payload.username
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="unsupported action",
            )
        try:
            exists = app.state.store.account_exists(payload.username)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="account not found",
            )
        issued = app.state.trust.issue("admin_provision", payload.username)
        return {"challenge": issued.value, "expires_in": issued.expires_in}

    @app.post("/api/checker/admin/provision")
    def checker_admin_provision(
        payload: CheckerProvisionRequest,
    ) -> dict[str, bool]:
        verified = app.state.trust.verify_and_consume(
            payload.challenge,
            payload.signature,
            scope="admin_provision",
            subject=payload.username,
        )
        if not verified:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="checker authentication failed",
            )
        try:
            provisioned = app.state.store.provision_admin(payload.username)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if not provisioned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="account not found",
            )
        return {"provisioned": True}

    @app.post("/api/adminsecret")
    def save_admin_secret(
        payload: AdminSecretRequest,
        session: Session = Depends(admin_session),
    ) -> dict[str, bool]:
        try:
            app.state.store.save_admin_secret(session.username, payload.flag)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        return {"stored": True}

    @app.get("/api/adminsecret")
    def load_admin_secret(
        session: Session = Depends(admin_session),
    ) -> dict[str, str]:
        try:
            value = app.state.store.load_admin_secret(session.username)
        except StorageUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="storage unavailable",
            ) from None
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="secret not found",
            )
        return {"secret": value}

    @app.post("/api/mission")
    async def upload_mission(request: Request) -> dict[str, bool]:
        body = await request.body()
        if len(body) > 65536:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="mission too large",
            )
        try:
            text = body.decode("utf-8")
            yaml.load(text, Loader=yaml.Loader)
        except (UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid mission",
            ) from None
        return {"accepted": True}

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        create_autopilot_app(),
        host="0.0.0.0",
        port=8888,
        workers=1,
        log_level="warning",
        lifespan="off",
    )
