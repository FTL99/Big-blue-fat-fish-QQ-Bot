"""Explicit Bilibili QR login; never print cookies, passwords or refresh tokens."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import tempfile
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, Request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "tests/private/bilibili-login-state.json"
IMAGE = ROOT / "tests/results/bilibili-login.png"
TARGET = Path("D:/qqaibot/astrbot/data/plugin_data/astrbot_plugin_group_chat_plus/bilibili/credential.json")
PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode/"
NAMES = {"SESSDATA", "bili_jct", "DedeUserID", "buvid3"}


def request(opener, url):
    if urlsplit(url).hostname not in {"passport.bilibili.com", "api.bilibili.com"}:
        raise ValueError("untrusted_login_endpoint")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    with opener.open(req, timeout=15) as response:
        data = json.load(response)
    if data.get("code") != 0:
        raise ValueError("bilibili_login_request_failed")
    return data.get("data") or {}


def credential_values(jar):
    return {cookie.name: cookie.value for cookie in jar
            if cookie.name in NAMES and cookie.domain.lstrip(".") in {"bilibili.com", "passport.bilibili.com", "api.bilibili.com"}}


def save_credentials(values):
    if not values.get("SESSDATA") or not values.get("DedeUserID"):
        raise ValueError("login_response_missing_cookies")
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=TARGET.parent, encoding="utf-8", delete=False) as output:
        json.dump(values, output, ensure_ascii=False)
        temporary = Path(output.name)
    temporary.replace(TARGET)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate", "wait"))
    parser.add_argument("--install-root", default="D:/qqaibot")
    args = parser.parse_args()
    global TARGET
    TARGET = Path(args.install_root) / "astrbot/data/plugin_data/astrbot_plugin_group_chat_plus/bilibili/credential.json"
    jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    if args.action == "generate":
        import qrcode
        data = request(opener, PASSPORT + "generate")
        if urlsplit(data["url"]).hostname not in {"passport.bilibili.com", "account.bilibili.com"}:
            raise ValueError("untrusted_qrcode_url")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        IMAGE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"key": data["qrcode_key"], "generated_at": time.time()}), encoding="utf-8")
        qrcode.make(data["url"]).save(IMAGE)
        print(json.dumps({"status": "qrcode_ready", "image": str(IMAGE), "valid_seconds": 180}), flush=True)
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    last_code = None
    while time.time() - state["generated_at"] < 180:
        data = request(opener, PASSPORT + "poll?" + urlencode({"qrcode_key": state["key"]}))
        code = data.get("code")
        if code == 0:
            identity = request(opener, "https://api.bilibili.com/x/web-interface/nav")
            if not identity.get("isLogin"):
                raise ValueError("login_not_confirmed")
            save_credentials(credential_values(jar))
            STATE.unlink(missing_ok=True)
            print(json.dumps({"status": "logged_in", "credential_saved": True}, ensure_ascii=False), flush=True)
            return
        if code == 86038:
            break
        if code not in (86101, 86090):
            raise ValueError("unexpected_login_status")
        if code != last_code:
            print(json.dumps({"status": "awaiting_confirmation" if code == 86090 else "awaiting_scan"}), flush=True)
            last_code = code
        time.sleep(3)
    print(json.dumps({"status": "qrcode_expired"}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Exceptions can contain signed URLs, so report only the exception class.
        print(json.dumps({"status": "error", "reason": type(error).__name__}), flush=True)
        raise SystemExit(1)
