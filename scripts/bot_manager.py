#!/usr/bin/env python3
"""
bot_manager.py — 一键启动/停止 AstrBot + NapCat（QQ AI 群聊机器人）

用法（部署全程 + 日常都在用，命令总览）:
    python bot_manager.py start          确保后台运行；已连接时直接返回
    python bot_manager.py scan           扫码模式：用 napcat.bat 开窗口出二维码（部署期 Phase 6 用）
    python bot_manager.py stop           全停：NapCat(QQ) 先，AstrBot 后
    python bot_manager.py status         查看两个服务当前状态
    python bot_manager.py kill_astrbot   只杀 AstrBot（含残留启动窗口）
    python bot_manager.py kill_napcat    只杀 NapCat（含残留启动窗口）

行为约定:
    - start 在服务已就绪时直接返回；需要启动时先清理残留，保持单实例。
      并发管理操作以部署目录中的文件锁隔离。
    - 进程定位一律按端口/命令行/可执行路径，**绝不按窗口标题**（标题匹配范围广会误杀，C6）。
    - 日常 start 在后台运行；scan 保留可见二维码窗口。进程清理不依赖窗口。

配置: 依次查 脚本目录 -> 脚本上一级（.bot_runtime 副本模式的部署目录）-> 当前目录 的
    deploy_state.json（优先）或 bot_manager.json（部署时由 agent 生成）:
    {
      "astrbot_root":      "D:\\qqaibot\\astrbot",     <- ASTRBOT_ROOT 指向的数据目录
      "astrbot_exe":       "C:\\Users\\xx\\.local\\bin\\astrbot.exe",
      "napcat_shell_dir":  "D:\\qqaibot\\napcat\\NapCat.52230.Shell",   <- 构建号随版本变，装完实测实际目录回填
      "napcat_root":       "D:\\qqaibot\\napcat"       <- 用于识别该目录下的 QQ.exe（避免误杀主号 QQ）
    }

设计说明:
    - 日常服务在后台运行，启动诊断写入 .bot_runtime 下的日志。
      重新扫码时单独显示 NapCat 窗口。
    - 部署期与日常全部用本脚本：start 起服务、scan 扫码、stop 停止；流程见 SKILL.md Phase 5/6。
    - 交付自包含：任意命令运行时把本脚本拷为 <部署目录>\\.bot_runtime\\bot_manager.py（运行时副本），
      双击 bat 用相对路径调它——skill 目录日后移动/更新/删除不影响已交付的机器人。
    - 标准库实现，无第三方依赖。
"""

import contextlib
import json
import msvcrt
import traceback
import os
import shutil
import subprocess
import sys
import tempfile
import time

CFG_NAMES = ("deploy_state.json", "bot_manager.json")
BAT_ASTRBOT = "start_astrbot.bat"
BAT_NAPCAT = "start_napcat.bat"
RUNTIME_DIR = ".bot_runtime"   # 启动 bat 的存放目录（内部产物，用户不需要碰）
CONSOLE_BAT = "机器人启动.bat"   # 用户双击入口：双击即启动，然后进菜单
RENAME_PS1 = "rename_qq_window.ps1"   # QQ 窗口改名辅助脚本（后台尽力而为）
RUNTIME_MGR = "bot_manager.py"   # 运行时副本：任意命令运行时拷到 .bot_runtime\ 下，双击 bat 调它

# QQ 窗口改名脚本（ASCII only——PS5.1 对无 BOM 文件按 ANSI 读，中文会乱码）。
# 原理：QQ 主窗口标题会被 QQ 自己重设（登录前后都变），所以轮询多轮用
# WM_SETTEXT 改成 qqaibot- 前缀的唯一标题；改名失败不影响任何功能
# （进程定位主力始终是 cmdline/路径，标题只是任务栏辨识 + kill 的补充手段）。
PS1_CONTENT = r'''
param(
  [string]$root = "",
  [string]$title = "qqaibot-QQ",
  [int]$tries = 12
)
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll", SetLastError=true)]
  public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);
}
'@
$rootL = $root.ToLower()
for ($i = 0; $i -lt $tries; $i++) {
  Start-Sleep -Seconds 5
  $procs = Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Where-Object {
    ($_.ExecutablePath -and $_.ExecutablePath.ToLower().Contains($rootL)) -or
    ($_.CommandLine -and $_.CommandLine.ToLower().Contains($rootL))
  }
  if (-not $procs) { continue }
  foreach ($p in $procs) {
    $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($gp -and $gp.MainWindowHandle -ne [IntPtr]::Zero) {
      $r = [IntPtr]::Zero
      [W]::SendMessageTimeout($gp.MainWindowHandle, 0x000C, [IntPtr]::Zero, $title, 2, 2000, [ref]$r) | Out-Null
    }
  }
  if ($i -ge 3) { break }   # 前几轮多改几次防 QQ 登录后覆盖，之后停
}
'''

DASH_PORT = "6185"   # AstrBot WebUI
WS_PORT = "6199"     # AstrBot 反向 WS（NapCat 连这里）


def log(msg):
    print(msg, flush=True)


def run_ps_pids(ps_pipeline, tag):
    """跑 PowerShell 查询返回 PID 列表，结果经临时文件中转。

    部分 agent 沙箱会把 PowerShell 的 stdout 吞掉（实测），导致探测/清理
    静默失效——status 假阴性、孤儿 cmd 壳窗口越积越多。把结果写进临时
    文件再读回来，绕开 stdout 通道。
    """
    out_path = os.path.join(tempfile.gettempdir(), "qqaibot_ps_%s.txt" % tag)
    full = "%s | Out-File -FilePath '%s' -Encoding utf8" % (ps_pipeline, out_path)
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", full],
                       capture_output=True, text=True, errors="replace", timeout=25)
    except Exception:
        pass
    pids = []
    try:
        # PS5.1 的 Out-File -Encoding utf8 带 BOM，utf-8-sig 兼容
        with open(out_path, encoding="utf-8-sig") as f:
            pids = [l.strip() for l in f if l.strip().isdigit()]
    except OSError:
        pass
    try:
        os.remove(out_path)
    except OSError:
        pass
    return pids


def norm_win_path(p):
    """Windows 路径归一化：正斜杠→反斜杠、去尾分隔符。

    deploy_state.json 里路径常写成 D:/x/y（正斜杠），而进程真实镜像路径、
    CommandLine 永远是反斜杠——直接拿来 Contains/-like 匹配必然漏（实测踩坑：
    QQ.exe 显示 PID - 但服务活着）。所有"拿 cfg 路径去匹配进程"的入口必须先过这里。
    """
    return (p or "").replace("/", "\\").strip().rstrip("\\")


def path_contains(hay, needle):
    """路径包含匹配：两边都归一化（正反斜杠折叠统一 + 小写）再比。

    等价于"正反斜杠各试一次"——配置可能写 D:/x（正斜杠），进程 API 返回
    D:\\x（反斜杠），两边风格无论怎么混都不会漏；比逐个形式去试更干净。
    """
    if not hay or not needle:
        return False
    return norm_win_path(needle).lower() in norm_win_path(hay).lower()


def load_config():
    # 依次找: 脚本同目录 -> 脚本上一级（.bot_runtime 副本模式的部署目录）-> 当前目录
    # deploy_state.json 优先，bot_manager.json 兼容旧部署
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.dirname(here), os.getcwd()):
        for name in CFG_NAMES:
            p = os.path.join(base, name)
            if os.path.exists(p):
                with open(p, encoding="utf-8-sig") as f:
                    return json.load(f), base
    log(f"[错误] 找不到 {' 或 '.join(CFG_NAMES)}（部署时由 agent 在 $INSTALL 下生成）")
    sys.exit(2)


def listening_pids(port):
    """返回监听指定端口的 PID 列表（netstat 解析，仅 LISTENING）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, errors="replace", timeout=15
        ).stdout
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        # 协议 本地地址 远程地址 状态 PID
        if len(parts) == 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            if parts[4].isdigit():
                pids.add(parts[4])
    return sorted(pids)


def pids_connected_to(port):
    """找与 127.0.0.1:<port> 建立 ESTABLISHED 连接的 PID（NapCat 的 QQ.exe 连 AstrBot 反向 WS）。

    netstat 不依赖 PowerShell——agent 沙箱里 PowerShell 探测可能失灵（假阴性），
    这条是兜底判定：连接还在 = NapCat 活着/没杀干净。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, errors="replace", timeout=15
        ).stdout
    except Exception:
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        # 协议 本地地址 远程地址 状态 PID
        if (len(parts) == 5 and parts[3] == "ESTABLISHED"
                and parts[2].endswith(f"127.0.0.1:{port}")):
            if parts[4].isdigit():
                pids.add(parts[4])
    return sorted(pids)


def qq_pids_by_image_path(napcat_root):
    """纯标准库 ctypes 枚举进程映像路径找 bot QQ——不 spawn 任何子进程。

    沙箱对 PowerShell 子进程的破坏形态多样（stdout 吞噬、命令行转义损坏导致
    ParserError），PowerShell 路线在沙箱里不可靠；Windows API 直调无此依赖。
    bot 的 QQ.exe 全部位于 NapCat.Shell 目录下，与主号（Program Files）路径
    天然不同，exe 路径匹配足以唯一区分，无需读 CommandLine。
    """
    import ctypes
    from ctypes import wintypes
    root = norm_win_path(napcat_root).lower()
    if not root:
        return []
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    # 64 位下必须声明签名：默认 int 返回值会截断 HANDLE 导致后续 API 全失败
    psapi.EnumProcesses.restype = wintypes.BOOL
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD),
                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    arr = (wintypes.DWORD * 8192)()
    cb = wintypes.DWORD(ctypes.sizeof(arr))
    pids = []
    if not psapi.EnumProcesses(ctypes.cast(arr, ctypes.POINTER(wintypes.DWORD)),
                               cb, ctypes.byref(cb)):
        return []
    for pid in arr:
        if not pid:
            continue
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            continue
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if path_contains(buf.value, root):
                    pids.append(str(pid))
        finally:
            k32.CloseHandle(h)
    return pids


def napcat_qq_pids(napcat_root):
    """找 bot 的 QQ.exe：exe 路径含 napcat 安装目录（与主号 Program Files 天然不同）。

    主力 ctypes 直调 Windows API（不 spawn 子进程，沙箱免疫）；
    PowerShell 单语句查询降为并集补充（无沙箱环境两者结果一致）。
    """
    root = norm_win_path(napcat_root).lower()
    if not root:
        return []
    result = set(qq_pids_by_image_path(root))
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='QQ.exe'\" | "
          "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Replace('/','\\').ToLower().Contains('%s') } | "
          "Select-Object -ExpandProperty ProcessId" % root.replace("'", "''"))
    result |= set(run_ps_pids(ps, "qq"))
    return sorted(result)


def bat_window_pids(where):
    """按 Where-Object 条件找 cmd.exe 的 PID（清理残留 bat 窗口用）。

    按 cmd.exe 的命令行内容匹配，比按窗口标题 taskkill /FI 精确得多（C6 误杀教训）。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
          "Where-Object { %s } | Select-Object -ExpandProperty ProcessId" % where)
    return run_ps_pids(ps, "batwin")


def kill_tree(pid):
    # errors="replace" 必须带：taskkill 成功时输出中文（GBK），strict 解码会把读线程炸掉
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, text=True, errors="replace")


def runtime_dir(base):
    """启动 bat 的运行时目录：收进 .bot_runtime\\，避免与用户文件混在安装根目录。"""
    d = os.path.join(base, RUNTIME_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def ensure_runtime_copy(base):
    """把本脚本拷贝为部署目录内的运行时副本（交付自包含的关键）。

    双击 bat 引用的是这个**副本**（相对路径 %~dp0），skill 目录日后被移动/更新/删除
    都不影响已交付的机器人——skill 内文件只用于部署期，长期运行一律用拷贝的副本。
    返回 True 表示可用相对路径引用副本；False 时调用方退回绝对路径（部署期兜底）。
    """
    dst = os.path.join(base, RUNTIME_DIR, RUNTIME_MGR)
    src = os.path.abspath(__file__)
    try:
        if os.path.abspath(dst).lower() != src.lower() and os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            return True
    except OSError:
        pass
    return os.path.isfile(dst)


def clean_legacy_bat(base, name):
    """清理旧版本直接生成在安装根目录的同名 bat（可能被运行中的 cmd 锁住，忽略失败）。"""
    legacy = os.path.join(base, name)
    try:
        if os.path.exists(legacy):
            os.remove(legacy)
            log(f"[清理] 移除根目录旧启动脚本: {name}")
    except OSError:
        pass


def ensure_console_bat(base, use_relative=True):
    """Generate hidden entries using Python directly, without cmd redirection."""
    manager = os.path.join(base, RUNTIME_DIR, RUNTIME_MGR) if use_relative else os.path.abspath(__file__)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = sys.executable
    entries = {"机器人后台启动.vbs": "start", "停止机器人.vbs": "stop",
               "查看机器人状态.vbs": "status", "机器人重新扫码.vbs": "scan"}
    for name, action in entries.items():
        command = subprocess.list2cmdline([pythonw, manager, action, "--desktop"])
        content = ('Option Explicit\nDim shell\n'
                   'Set shell = CreateObject("WScript.Shell")\n'
                   'shell.Run "' + command.replace('"', '""') + '", 0, False\n')
        with open(os.path.join(base, name), "w", encoding="utf-16") as f:
            f.write(content)
    content = ('@echo off\r\n'
               'start "" "%SystemRoot%\\System32\\wscript.exe" "%~dp0机器人后台启动.vbs"\r\n'
               'exit /b 0\r\n')
    with open(os.path.join(base, CONSOLE_BAT), "w", encoding="gbk") as f:
        f.write(content)


def cmd_status(cfg, base=None):
    dash = listening_pids(DASH_PORT)
    ws = listening_pids(WS_PORT)
    nc_root = norm_win_path(cfg.get("napcat_root"))
    # WS 连接兜底：沙箱里 PowerShell 探测可能失灵（假阴性 STOPPED），
    # AstrBot 在监听时若有 ESTABLISHED 到 6199 的连接，NapCat 就是活的
    ws_clients = pids_connected_to(WS_PORT) if dash else []
    if nc_root:
        qq = napcat_qq_pids(nc_root)
        qq_line = f"QQ.exe PID {','.join(qq) or '-'}"
        qq_state = "RUNNING" if (qq or ws_clients) else "STOPPED"
        if not qq and ws_clients:
            qq_line += f" + WS连接 PID {','.join(ws_clients)}（PowerShell 探测失灵，按连接兜底判定）"
    else:
        qq, qq_line, qq_state = [], "未配置（Phase 6 后回填）", "N/A"
    log(f"AstrBot : {'RUNNING' if dash else 'STOPPED'}"
        f"  (WebUI:{DASH_PORT} {'监听中' if dash else '无'}, 反向WS:{WS_PORT} {'监听中' if ws else '无'}, PID {','.join(dash) or '-'})")
    log(f"NapCat  : {qq_state}  ({qq_line})")
    log("整体状态: " + ("正常运行" if dash and (qq or ws_clients) else ("部分未启动" if (dash or qq or ws_clients) else "全部未启动")))
    return 0 if (dash and (qq or ws_clients)) else 1


def cmd_kill_astrbot(cfg, base=None):
    """只杀 AstrBot：监听 6185/6199 的进程 + start_astrbot.bat 残留窗口。"""
    dash = listening_pids(DASH_PORT)
    ws = [p for p in listening_pids(WS_PORT) if p not in dash]
    for pid in dash + ws:
        kill_tree(pid)
    log(f"[清理] AstrBot: {'已杀 ' + str(len(dash) + len(ws)) + ' 个进程' if dash or ws else '未在运行'}")

    bats = bat_window_pids("$_.CommandLine -like '*%s*'" % BAT_ASTRBOT)
    for pid in bats:
        kill_tree(pid)
    log(f"[清理] 残留启动窗口: {'已杀 ' + str(len(bats)) + ' 个' if bats else '无'}")

    left = listening_pids(DASH_PORT)
    log("[复查] AstrBot 端口 " + ("仍被占用（PID %s）" % ",".join(left) if left else "已清空"))
    return 0 if not left else 1


def ensure_rename_ps1(runtime):
    """落地 QQ 窗口改名辅助脚本（ASCII only，避免 PS5.1 无 BOM 编码坑）。"""
    p = os.path.join(runtime, RENAME_PS1)
    with open(p, "w", encoding="ascii", errors="replace") as f:
        f.write(PS1_CONTENT)
    return p


def qq_pids_by_title():
    """按唯一窗口标题前缀找 bot 的 QQ.exe（qqaibot- 前缀含 QQ 号，精确无 C6 误杀风险）。

    作为 cmdline/路径匹配的补充兜底——只认我们亲手改过的标题前缀。
    """
    ps = ("Get-Process -Name QQ -ErrorAction SilentlyContinue | "
          "Where-Object { $_.MainWindowTitle -like 'qqaibot-*' } | "
          "Select-Object -ExpandProperty Id")
    return run_ps_pids(ps, "qqtitle")


def cmd_kill_napcat(cfg, base=None):
    """只杀 NapCat：napcat 目录下的 QQ.exe（cmdline/路径/唯一标题三重定位）+ napcat 相关 bat 残留窗口。"""
    nc_root = norm_win_path(cfg.get("napcat_root"))
    if nc_root:
        qq = napcat_qq_pids(nc_root)
        by_title = [p for p in qq_pids_by_title() if p not in qq]
        for pid in qq + by_title:
            kill_tree(pid)
        msg = f"已杀 {len(qq) + len(by_title)} 个进程" if (qq or by_title) else "未在运行"
        if by_title:
            msg += f"（{len(by_title)} 个按窗口标题定位）"
        log(f"[清理] NapCat(QQ.exe): {msg}")
        # napcat 目录下一切 .bat 的 cmd 窗口 + 我们在 .bot_runtime 下生成的启动/扫码窗口
        # （后者 cmdline 不含 napcat_root，必须按 bat 名补一条，否则 scan/start 窗口清不掉）
        root = nc_root.rstrip("\\").replace("'", "''")
        where = ("($_.CommandLine -like '*%s*' -and $_.CommandLine -like '*.bat*') "
                 "-or $_.CommandLine -like '*start_napcat*'" % root)
    else:
        log("[清理] NapCat 未配置（napcat_root 为空）——只清理本脚本自己的启动窗口")
        where = "$_.CommandLine -like '*%s*'" % BAT_NAPCAT

    bats = bat_window_pids(where)
    for pid in bats:
        kill_tree(pid)
    log(f"[清理] NapCat 相关 bat 窗口: {'已杀 ' + str(len(bats)) + ' 个' if bats else '无'}")

    left = set(napcat_qq_pids(nc_root) if nc_root else [])
    left |= set(pids_connected_to(WS_PORT))   # 连接还在 = 没杀干净（netstat 兜底，不依赖 PowerShell）
    left = sorted(left)
    log("[复查] NapCat 进程 " + ("仍有残留（PID %s）" % ",".join(left) if left else "已清空"))
    return 0 if not left else 1


def launch_background(command, cwd, logfile, env=None):
    """Start a service directly; batch files still use the Windows command shell."""
    if isinstance(command, str):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", command]
    with open(logfile, "wb") as output:
        return subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)


def cmd_start(cfg, base):
    if listening_pids(DASH_PORT) and listening_pids(WS_PORT) and pids_connected_to(WS_PORT):
        log("[就绪] 机器人已在运行，QQ 已连接；无需重复启动。")
        return 0
    root = cfg["astrbot_root"]
    python = cfg.get("astrbot_python")
    command = [python, "-m", "astrbot.cli", "run"] if python else [cfg["astrbot_exe"], "run"]

    # 未就绪时清理残留，再保持单实例启动。
    cmd_kill_astrbot(cfg, base)
    time.sleep(2)
    clean_legacy_bat(base, BAT_ASTRBOT)
    env = os.environ.copy()
    env["ASTRBOT_ROOT"] = root
    env["PYTHONIOENCODING"] = "utf-8"
    if python:
        env["PATH"] = os.path.dirname(python) + os.pathsep + env.get("PATH", "")
    process = launch_background(command, root, os.path.join(runtime_dir(base), "astrbot-console.log"), env=env)
    log("[启动] AstrBot 已在后台启动，等待 WebUI 就绪 ...")
    for _ in range(60):
        time.sleep(2)
        if listening_pids(DASH_PORT):
            log("[就绪] AstrBot WebUI 已监听")
            break
        if process.poll() is not None:
            log(f"[错误] AstrBot 启动进程已退出（代码 {process.returncode}），请检查 .bot_runtime/astrbot-console.log")
            return 1
    else:
        log("[错误] 120 秒内 WebUI 未就绪，请检查 .bot_runtime/astrbot-console.log")
        return 1

    nc_root = (cfg.get("napcat_root") or "").strip()
    shell_dir = norm_win_path(cfg.get("napcat_shell_dir"))
    if not nc_root or not shell_dir:
        log("[跳过] NapCat 未配置（napcat_root/napcat_shell_dir 为空，Phase 6 装完回填）——本次只启动 AstrBot")
    else:
        # 同样先杀一次再启动（同号多开必互踢，C5）
        cmd_kill_napcat(cfg, base)
        time.sleep(3)
        if not os.path.isdir(shell_dir):
            log(f"[错误] NapCat Shell 目录不存在: {shell_dir}")
            return 1
        clean_legacy_bat(base, BAT_NAPCAT)
        bat = os.path.join(runtime_dir(base), BAT_NAPCAT)
        with open(bat, "w", encoding="gbk", errors="replace") as f:
            f.write(f'@echo off\r\ntitle qqaibot-NapCat\r\ncd /d "{shell_dir}"\r\n'
                    f'call napcat.quick.bat\r\n')
        launch_background(bat, shell_dir, os.path.join(runtime_dir(base), "napcat-console.log"))
        log("[启动] NapCat 已在后台启动（quick 免扫码；需要重新登录时使用 scan）")
        # 后台尽力把 bot 的 QQ 窗口标题改成 qqaibot-QQ-<QQ号>（防与主号 QQ 混淆）；
        # QQ 登录前后会自己重设标题，ps1 内部轮询多轮；失败不影响功能
        qq_no = str(cfg.get("qq") or "").strip()
        qq_title = f"qqaibot-QQ-{qq_no}" if qq_no else "qqaibot-QQ"
        try:
            ps1 = ensure_rename_ps1(runtime_dir(base))
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", ps1, "-root", nc_root, "-title", qq_title],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            log(f"[启动] bot 的 QQ 窗口将在登录后自动改名为 {qq_title}（任务栏辨识用，失败不影响功能）")
        except Exception as e:
            log(f"[提示] QQ 窗口改名任务未能启动（不影响功能）: {e}")
        for _ in range(30):
            if pids_connected_to(WS_PORT):
                log("[就绪] NapCat 已连接 AstrBot")
                break
            time.sleep(2)
        else:
            log("[提示] QQ 连接尚未就绪，请检查 .bot_runtime/napcat-console.log；"
                "登录失效时使用机器人重新扫码.vbs")
            return 1
    log("[完成] 启动流程已执行。停止请用: python bot_manager.py stop")
    return 0


def cmd_scan(cfg, base):
    """扫码模式：用 napcat.bat（非 quick）开窗口出二维码。

    部署期 agent 用这个启动扫码，**禁止自己手动 cd + start napcat.bat**——
    手动跑的路径/cwd 事故（「Windows 找不到文件 napcat.bat」、bootmain 陷阱）全由本命令规避。
    """
    shell_dir = norm_win_path(cfg.get("napcat_shell_dir"))
    if not shell_dir or not os.path.isdir(shell_dir):
        log(f"[错误] napcat_shell_dir 未回填或不存在: {shell_dir}——先完成 Phase 6 安装并把目录写进 deploy_state.json")
        return 1
    real = os.path.join(shell_dir, "napcat.bat")
    if not os.path.isfile(real):
        log(f"[错误] {real} 不存在——检查 napcat_shell_dir 是否指向 NapCat.*.Shell 目录（别指向 bootmain）")
        return 1
    cmd_kill_napcat(cfg, base)
    time.sleep(2)
    clean_legacy_bat(base, BAT_NAPCAT)
    bat = os.path.join(runtime_dir(base), BAT_NAPCAT)
    with open(bat, "w", encoding="gbk", errors="replace") as f:
        f.write(f'@echo off\r\ntitle qqaibot-NapCat\r\ncd /d "{shell_dir}"\r\n'
                f'call napcat.bat\r\npause\r\n')
    subprocess.run(["cmd", "/c", "start", "", bat], check=False)  # 空标题，同 AstrBot 处的坑
    log("[扫码] NapCat 窗口已打开等待出二维码；出码后让用户用小号扫码（不出码查 pitfalls C4，找 qrcode.png）")
    log("[扫码] 登录成功后此窗口即服务本体，保留；日常重启换用 start（走 quick.bat 免扫码）")
    return 0


def cmd_stop(cfg, base=None):
    napcat_result = cmd_kill_napcat(cfg, base)
    time.sleep(1)
    astrbot_result = cmd_kill_astrbot(cfg, base)
    time.sleep(1)
    cmd_status(cfg)
    return max(napcat_result, astrbot_result)


def execute_command(cmd, cfg, base):
    # One deployment has one service pair; serialize lifecycle operations.
    with open(os.path.join(runtime_dir(base), "manager.lock"), "a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            log("[提示] 已有启动或管理操作正在进行，本次不重复执行。")
            return 0
        try:
            use_rel = ensure_runtime_copy(base)
            ensure_console_bat(base, use_rel)
            return {"start": cmd_start, "stop": cmd_stop, "status": cmd_status,
                    "scan": cmd_scan, "kill_astrbot": cmd_kill_astrbot,
                    "kill_napcat": cmd_kill_napcat}[cmd](cfg, base)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def desktop_command(cmd, cfg, base):
    directory = runtime_dir(base)
    logfile = os.path.join(directory, f"manager-{cmd}-{time.time_ns()}-{os.getpid()}.log")
    with open(logfile, "w", encoding="utf-8") as output:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                result = execute_command(cmd, cfg, base)
            except Exception:
                traceback.print_exc()
                result = 1
    # Preserve each attempt; the familiar log path is a completed snapshot only.
    try:
        snapshot = logfile + ".tmp"
        shutil.copyfile(logfile, snapshot)
        os.replace(snapshot, os.path.join(directory, f"manager-{cmd}.log"))
    except OSError:
        pass  # The per-attempt log remains available if the snapshot is locked.
    if result or cmd == "status":
        import ctypes
        with open(logfile, encoding="utf-8") as output:
            message = output.read(4000).strip()
        message = (message or f"启动管理器退出代码：{result}") + "\n\n日志：" + logfile
        ctypes.windll.user32.MessageBoxW(None, message, "QQ机器人", 16 if result else 64)
    return result


def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if cmd not in ("start", "stop", "status", "scan", "kill_astrbot", "kill_napcat"):
        print(__doc__)
        return 2
    cfg, base = load_config()
    if "--desktop" in sys.argv[2:]:
        return desktop_command(cmd, cfg, base)
    return execute_command(cmd, cfg, base)


if __name__ == "__main__":
    sys.exit(main())
