from base64 import b64decode
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


attack_data = "attack data"

_BLACKBOX_AAD = b"carhack:blackbox:v1"

DEFAULT_TELE_KEY = b"CarHack-telemetry-aes-key-v1-32b"

cipher = AESGCM(DEFAULT_TELE_KEY)
record = b64decode(
    requests.get(f"http://localhost:8080/api/blackbox/raw/{attack_data}")
    .json()
    .get("ct_b64")
)

plaintext = cipher.decrypt(record[:12], record[12:], _BLACKBOX_AAD)
print(plaintext)
