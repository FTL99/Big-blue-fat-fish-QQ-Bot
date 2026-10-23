"""Local QQ login/connection health and a single-instance Windows notifier."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request


def listening(port):
    with socket.socket() as client:
        client.settimeout(1)
        return client.connect_ex(('127.0.0.1', port)) == 0


class NapCatHealth:
    def __init__(self, base):
        self.base = Path(base)
        cfg = json.loads((self.base / 'deploy_state.json').read_text(encoding='utf-8-sig'))
        shell = Path(cfg['napcat_shell_dir'])
        paths = list(shell.glob('versions/*/resources/app/napcat/config/webui.json'))
        if len(paths) != 1:
            raise RuntimeError('Cannot uniquely locate NapCat WebUI configuration')
        self.config_path = paths[0]
        self.expected_qq = str(cfg['qq'])
        self.credential = None
        self.expires = 0

    def post(self, path, body):
        req = urllib.request.Request(self.url + path,
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
            **({'Authorization': 'Bearer ' + self.credential} if self.credential else {})})
        with urllib.request.urlopen(req, timeout=4) as response:
            result = json.load(response)
        if result.get('code') != 0:
            self.credential = None
            raise RuntimeError('NapCat management request failed')
        return result.get('data')

    def check(self):
        try:
            if not self.credential or time.monotonic() >= self.expires:
                cfg = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
                self.url = 'http://127.0.0.1:' + str(cfg['port']) + '/api'
                hashed = hashlib.sha256((cfg['token'] + '.napcat').encode()).hexdigest()
                self.credential = self.post('/auth/login', {'hash': hashed})['Credential']
                self.expires = time.monotonic() + 3000
            status = self.post('/QQLogin/CheckLoginStatus', {})
            if not status.get('isLogin') or not status.get('coreReady'):
                phase = status.get('loginPhase')
                if phase == 'waiting_qrcode':
                    return {'online': False, 'reason': 'QQ 登录已失效或尚未登录，请用机器人小号重新扫码',
                            'phase': phase}
                reason = 'QQ 正在自动重连，暂不要求重新登录' if phase == 'reconnecting' else 'QQ 登录状态尚未就绪，暂不判断需要重新登录'
                return {'online': None, 'reason': reason, 'phase': phase}
            account = self.post('/QQLogin/GetQQLoginInfo', {})
            if str(account.get('uin')) != self.expected_qq:
                return {'online': False, 'reason': '当前登录的 QQ 不是机器人小号'}
            if not listening(6185) or not listening(6199):
                return {'online': False, 'reason': 'AstrBot 服务未就绪'}
            # A listening server alone does not prove NapCat is connected to it.
            result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True,
                text=True, errors='replace', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            connected = any(len(parts) == 5 and parts[3] == 'ESTABLISHED'
                and parts[2] == '127.0.0.1:6199'
                for parts in (line.split() for line in result.stdout.splitlines()))
            return {'online': connected, 'reason': 'QQ 已在线且已连接 AstrBot' if connected
                    else 'QQ 已在线，但尚未连接 AstrBot'}
        except Exception as exc:
            return {'online': None, 'reason': '无法确认 QQ 在线状态（本机管理接口不可用）',
                    'error_type': type(exc).__name__}


class AlertState:
    def __init__(self):
        self.failures = 0
        self.alerted = False
        self.kind = None

    def update(self, online, paused=False):
        if online is True or paused:
            self.failures = 0
            self.alerted = False
            self.kind = None
            return False
        kind = 'unknown' if online is None else 'offline'
        if kind != self.kind:
            self.failures = 0
            self.alerted = False
            self.kind = kind
        self.failures += 1
        if self.failures >= 2 and not self.alerted:
            self.alerted = True
            return True
        return False


def popup(reason, unknown=False):
    import ctypes
    if unknown:
        text = ('暂时无法确认 QQ 在线状态。这不代表机器人已掉线，请勿因此重新扫码。\n\n' + reason +
                '\n\n监控会继续重试；如果持续出现，请检查 NapCat 本机管理接口。')
    else:
        text = '机器人目前不能正常收发群消息。\n\n' + reason
        if 'QQ 登录已失效' in reason or '当前登录的 QQ 不是机器人小号' in reason:
            text += ('\n\n请打开 http://127.0.0.1:6099 ，用机器人小号 机器人QQ账号 重新登录。'
                     '\n登录恢复后会静默补读群消息，不补发回复。')
        else:
            text += '\n\n请检查 NapCat 与 AstrBot 的连接，监控会继续重试。'
    ctypes.windll.user32.MessageBoxW(None, text, 'QQ 机器人掉线提醒', 0x40 | 0x10000)


def start_watchdog(base):
    base = Path(base)
    (base / '.bot_runtime' / 'qq-watch-paused').unlink(missing_ok=True)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable),
        str(Path(__file__).resolve()), 'watch', str(base)], cwd=base,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)


def watch(base):
    import msvcrt
    runtime = Path(base) / '.bot_runtime'
    with (runtime / 'qq-watch.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        health, alerts = NapCatHealth(base), AlertState()
        previous = None
        popup_thread = None
        while True:
            paused = (runtime / 'qq-watch-paused').exists()
            status = health.check() if not paused else {'online': False, 'reason': '用户已停止机器人'}
            state = {**status, 'paused': paused}
            report = {**state, 'checked_at': time.time(), 'pid': os.getpid()}
            temp = runtime / 'qq-health.json.tmp'
            temp.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            temp.replace(runtime / 'qq-health.json')
            if state != previous:
                with (runtime / 'qq-health-events.jsonl').open('a', encoding='utf-8') as out:
                    out.write(json.dumps(report, ensure_ascii=False) + '\n')
                previous = state
            if alerts.update(status['online'], paused) and not (popup_thread and popup_thread.is_alive()):
                popup_thread = threading.Thread(target=popup,
                    args=(status['reason'], status['online'] is None), daemon=True)
                popup_thread.start()
            time.sleep(15)


if __name__ == '__main__':
    watch(sys.argv[2])
