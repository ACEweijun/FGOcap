# -*- coding: utf-8 -*-
"""
FGO 国服一键抓包（自动版）
=========================
设计目标：全程无需键盘输入，只靠「弹窗点确定」推进流程。

流程：
  1. 欢迎 + 检查模拟器 adb（自动定位雷电/常见模拟器）
  2. 启动 mitmdump 抓包服务（后台）
  3. 自动获取电脑局域网 IP 并设置模拟器代理
  4. 弹窗提示：请登录 FGO 到地球仪/公告页
  5. 自动轮询 toplogin 目录，发现新 json 即成功
  6. 自动清理：清除代理 + 停止抓包 + 提示导入 Chaldea

用法：双击「一键抓包.bat」即可。任何一步失败都会弹窗说明原因。
"""

import ctypes
import glob
import hashlib
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# 让 cmd 窗口实时显示进度（line-buffered stdout）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# 配置（一般无需修改）
# ---------------------------------------------------------------------------
TOOLS_DIR = Path(__file__).resolve().parent          # tools 目录
ADDON = TOOLS_DIR / "fgoaddon.py"
TOPLOGIN_DIR = TOOLS_DIR / "toplogin"
PORT = 18080


def resolve_mitmdump():
    """返回 mitmdump 启动命令（列表）。

    查找顺序：
      1) 本地免安装目录 tools/mitmproxy-12.2.3/mitmdump.exe（兼容旧工具包）
      2) PATH 中的 mitmdump / mitmdump.exe
      3) Python 同目录的 Scripts/mitmdump.exe（pip install 后落这里，但通常不在 PATH）
      4) 仍找不到：抛 FileNotFoundError（main 弹窗会给出修复指引）
    """
    local = TOOLS_DIR / "mitmproxy-12.2.3" / "mitmdump.exe"
    if local.is_file():
        return [str(local)]
    import shutil
    for name in ("mitmdump.exe", "mitmdump"):
        p = shutil.which(name)
        if p:
            return [p]
    # pip install mitmproxy 后的可执行文件在 Python 同目录的 Scripts/，
    # 但 Windows 默认不会把这个目录加到 PATH → shutil.which 找不到，
    # 必须手动到这个目录找（这是绝大多数新用户的实际情况）
    scripts_dir = Path(sys.executable).parent / "Scripts"
    for name in ("mitmdump.exe", "mitmdump"):
        p = scripts_dir / name
        if p.is_file():
            return [str(p)]
    raise FileNotFoundError(
        "未找到 mitmdump 可执行文件！请二选一：\n"
        "  1) pip install mitmproxy（推荐）\n"
        "  2) 解压官方版 mitmproxy 12.2.3 到 tools/mitmproxy-12.2.3/"
    )


# 惰性解析：不在模块导入时跑（避免 resolve_mitmdump 抛错导致整个模块 import 失败、
# main() 进不去、弹窗都不会弹）。start_mitmdump() 第一次被调用时再解析并缓存。
_MITMDUMP_CMD = None
def _get_mitmdump_cmd():
    global _MITMDUMP_CMD
    if _MITMDUMP_CMD is None:
        _MITMDUMP_CMD = resolve_mitmdump()
    return _MITMDUMP_CMD

# 雷电安装路径（自动探测）
LDPLAYER_PATHS = [
    r"G:\leidian\LDPlayer9\adb.exe",
    r"D:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"D:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files\Netease\MuMu Player 12\nx_main\adb.exe",
    r"D:\Program Files\Netease\MuMu Player 12\nx_main\adb.exe",
    r"C:\Program Files\Nox\bin\adb.exe",
]
# ---- FGO 包名：自动探测为主，config.ini 可覆盖 ----
# 每个人区服/渠道不同，还有人装了多个 FGO，所以不写死单一包名：
#   1) tools/config.ini 里写了 package=xxx → 优先用用户指定的
#   2) 否则自动列出模拟器里所有 FGO 相关包，按下面候选顺序挑第一个已安装的
#   3) 候选都没命中 → 用探测到的第一个 / 兜底默认值
FGO_PACKAGE_CANDIDATES = [
    "com.bilibili.fgo.qihoo",        # 国服 360 渠道服（默认）
    "com.bilibili.fategrandorder",   # 国服其他渠道
    "com.bilibili.fatego",           # 国服 B 服
    "com.aniplex.fategrandorder",    # 日服 / 台服
]
FGO_PACKAGE = FGO_PACKAGE_CANDIDATES[0]   # 兜底默认值（探测失败时用）
CONFIG_FILE = TOOLS_DIR / "config.ini"    # 可选：装了多个 FGO 时在这里指定用哪个

# ---------------------------------------------------------------------------
# 弹窗工具
# ---------------------------------------------------------------------------
MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONINFO = 0x00000040
MB_ICONWARN = 0x00000030
MB_ICONERR = 0x00000010
MB_TOPMOST = 0x00040000
MB_SYSTEMMODAL = 0x00001000


def msg(text, title="FGO 一键抓包", style=MB_OK | MB_ICONINFO | MB_SYSTEMMODAL | MB_TOPMOST):
    """弹窗并等待用户点击确定，返回点击结果。"""
    return ctypes.windll.user32.MessageBoxW(None, text, title, style)


def info(text):
    msg(text)


def warn(text):
    msg(text, style=MB_OK | MB_ICONWARN | MB_SYSTEMMODAL | MB_TOPMOST)


def error(text):
    msg(text, style=MB_OK | MB_ICONERR | MB_SYSTEMMODAL | MB_TOPMOST)


def ask(text):
    """弹窗询问是/否，返回 True/False。"""
    r = ctypes.windll.user32.MessageBoxW(
        None, text, "FGO 一键抓包", MB_YESNO | MB_ICONWARN | MB_SYSTEMMODAL | MB_TOPMOST
    )
    return r == 6  # IDYES


# ---------------------------------------------------------------------------
# adb 工具
# ---------------------------------------------------------------------------
def find_adb():
    """探测模拟器 adb 路径，返回第一个存在的。"""
    # 雷电：通配 G:\leidian\LDPlayer*\adb.exe（兼容 LDPlayer9/12/13/14/15 等任意版本号）
    # glob 默认字典序，LDPlayer1*/LDPlayer20 会优先于 LDPlayer9；
    # 绝大多数情况只装一个，命中即返回
    for adb in sorted(glob.glob(r"G:\leidian\LDPlayer*\adb.exe")):
        if os.path.isfile(adb):
            return adb
    # 兜底：硬编码路径（MuMu/Nox 等非雷电模拟器 + 雷电装在 C/D 等其他盘符）
    for p in LDPLAYER_PATHS:
        if os.path.isfile(p):
            return p
    # 兜底：PATH 环境变量里的 adb
    for d in os.environ.get("PATH", "").split(";"):
        if not d:
            continue
        cand = os.path.join(d, "adb.exe")
        if os.path.isfile(cand):
            return cand
    return None


def adb_shell(adb, args, serial=None, timeout=60):
    """调用 adb shell 执行一条命令。

    【bug fix 2026-08-31】去掉调用方传 'shell' 前缀的冗余：
    老代码 adb_shell(adb, ["shell", "am", "start", ...]) 在函数内部追加 ["shell"]
    后变成 `adb -s X shell shell am start ...`，目标 shell 报 `shell: not found`，
    force-stop / am start / pidof 全部失效。
    兼容：自动剥离 args 第一个元素如果是 "shell"，这样历史调用不会报错。
    """
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    # 自动去冗余的 "shell" 前缀（旧调用兼容）
    args = list(args)
    if args and args[0] == "shell":
        args = args[1:]
    cmd += ["shell"] + args
    try:
        r = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：避免弹 cmd 窗口
        )
        out = r.stdout
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return out.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return out.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def find_serial(adb):
    """返回第一个状态为 device/offline/unauthorized 的模拟器 serial，无则 None。

    offline / unauthorized 表示模拟器在连接中（adb 还没握手完成），也接受。
    """
    try:
        r = subprocess.run(
            [adb, "devices"], capture_output=True, text=True, timeout=20
        )
        for line in r.stdout.splitlines()[1:]:
            line = line.strip()
            if not line or "daemon" in line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized"):
                return parts[0]
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# FGO 包名 / 启动入口 自动探测
# ---------------------------------------------------------------------------
def load_package_override():
    """从 tools/config.ini 读取用户指定的 FGO 包名（可选）。

    config.ini 示例（装了多个 FGO 时用）：
        package=com.bilibili.fgo.qihoo
    """
    try:
        if not CONFIG_FILE.is_file():
            return None
        for line in CONFIG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.lower().startswith("package"):
                val = line.split("=", 1)[1].strip()
                if val:
                    return val
    except Exception:
        pass
    return None


def load_autotap_config():
    """从 tools/config.ini 读取自动点击登录配置（可选）。

    config.ini 示例：
        auto_tap=1            # 1=开启自动点击登录（默认），0=关闭（自己手点）
        tap_interval=1.5      # 点击间隔（秒，默认 1.5）
        tap_timeout=90        # 持续多久没抓到就结束（秒，默认 90）

    实测：脚本启动 mitmdump→FGO 首次联网约 13s，联网→toplogin 落地约 27s，
    单次完整约 40s，故 90s 留足 2 倍余量。
    """
    cfg = {"auto_tap": True, "tap_interval": 1.5, "tap_timeout": 90}
    try:
        if not CONFIG_FILE.is_file():
            return cfg
        for line in CONFIG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "auto_tap":
                cfg["auto_tap"] = v not in ("0", "false", "no", "off")
            elif k == "tap_interval":
                try:
                    cfg["tap_interval"] = float(v)
                except Exception:
                    pass
            elif k == "tap_timeout":
                try:
                    cfg["tap_timeout"] = int(float(v))
                except Exception:
                    pass
    except Exception:
        pass
    return cfg


def auto_tap_worker(adb, serial, interval, duration, stop_flag):
    """FGO 启动后定时点击屏幕正中央，帮用户自动点登录。

    stop_flag: dict，主流程抓到 toplogin 后置 {"done": True} 让本线程退出。
    """
    try:
        size_out = adb_shell(adb, ["shell", "wm", "size"], serial, timeout=15)
        m = re.search(r"(\d+)\s*x\s*(\d+)", size_out)
        if m:
            w, h = int(m.group(1)), int(m.group(2))
        else:
            w, h = 720, 1280
        cx, cy = w // 2, h // 2
        print(f"[*] 自动点击已启动：每 {interval}s 点屏幕中央 ({cx},{cy})，"
              f"最多 {duration}s", flush=True)
        deadline = time.time() + duration
        while time.time() < deadline and not stop_flag.get("done"):
            adb_shell(adb, ["shell", "input", "tap", str(cx), str(cy)],
                      serial, timeout=10)
            time.sleep(interval)
        print("[*] 自动点击已停止", flush=True)
    except Exception as e:
        print(f"[!] 自动点击异常：{e}", flush=True)


def detect_fgo_packages(adb, serial):
    """列出模拟器里已安装的 FGO 相关包名（按 fate / fgo 关键词匹配）。"""
    out = adb_shell(adb, ["pm", "list", "packages"], serial, timeout=30)
    pkgs = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("package:"):
            continue
        pkg = line[len("package:"):].strip()
        low = pkg.lower()
        if "fate" in low or "fgo" in low:
            pkgs.append(pkg)
    return pkgs


def resolve_fgo_package(adb, serial):
    """决定用哪个 FGO 包名。

    优先级：config.ini 指定 > 候选表里已安装的（按顺序）> 探测到的第一个 > 兜底默认值。
    """
    override = load_package_override()
    if override:
        print(f"[*] FGO 包名（config.ini 指定）: {override}", flush=True)
        return override
    installed = detect_fgo_packages(adb, serial)
    if not installed:
        print(f"[!] 未探测到 FGO 包，使用默认: {FGO_PACKAGE}", flush=True)
        return FGO_PACKAGE
    for cand in FGO_PACKAGE_CANDIDATES:
        if cand in installed:
            if len(installed) > 1:
                print(f"[*] 检测到多个 FGO 包: {installed}", flush=True)
                print(f"    自动选用: {cand}", flush=True)
                print(f"    想换别的包？在 tools/config.ini 写: package=包名", flush=True)
            else:
                print(f"[*] FGO 包名: {cand}", flush=True)
            return cand
    print(f"[*] 候选表未命中，使用探测到的第一个: {installed[0]}", flush=True)
    return installed[0]


def resolve_launcher(adb, serial, pkg):
    """查询目标包的 launcher activity（不再硬编码 SplashActivity——各区服入口不同）。"""
    out = adb_shell(
        adb, ["cmd", "package", "resolve-activity", "--brief", pkg],
        serial, timeout=20,
    )
    for line in out.splitlines():
        line = line.strip()
        if "/" in line and line.startswith(pkg):
            return line
    return None


# ---------------------------------------------------------------------------
# 网络工具
# ---------------------------------------------------------------------------
def get_lan_ip():
    """获取电脑局域网 IP。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def port_listening(port):
    """检查端口是否被监听。"""
    try:
        r = subprocess.run(
            ["netstat", "-ano"], capture_output=True, timeout=20
        )
        out = r.stdout
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                text = out.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = out.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                return True
    except Exception:
        pass
    return False


def verify_proxy_ready(adb, serial, ip, port):
    """启动 FGO 前的代理自检，返回 (value_ok, icmp_ok, reason)。

    【背景 2026-09-06】雷电14 首跑出现 FGO"连接失败，请检查网络"：
    代理写入失败/未生效时 FGO 带着死代理启动必然弹此错。

    value_ok=False → 硬伤：http_proxy 读回不对（写入失败/残留旧值），
                     必须重试或阻断启动（否则 FGO 必然连不上）。
    value_ok=True, icmp_ok=False → 值已写对但 ICMP 不通：只告警不阻断。
        原因：Windows 防火墙默认拦 ICMP 回声，而 TCP(18080) 不受影响；
        把 ICMP 当硬门槛会在 NAT 模式下误伤（值写对了也启动不了 FGO，
        比原始故障更糟）。
    """
    # 1) 代理值读回比对（硬门槛）
    cur = adb_shell(adb, ["settings", "get", "global", "http_proxy"], serial).strip()
    if cur != f"{ip}:{port}":
        return False, False, f"http_proxy 读回 {cur!r}，期望 {ip}:{port}（写入未生效）"
    # 2) 模拟器 -> 主机连通性（ICMP，仅诊断，不阻断）
    try:
        out = adb_shell(adb, ["shell", f"ping -c 2 -W 2 {ip}"], serial, timeout=20)
        low = out.lower()
        if "ttl=" not in low and "received" not in low:
            return True, False, f"ping {ip} 无输出（Windows 防火墙常拦 ICMP，TCP 走代理不受影响）"
        if "100% packet loss" in low or "0 received" in low:
            return True, False, f"ping {ip} 丢包（Windows 防火墙常拦 ICMP，TCP 走代理不受影响）"
    except Exception as e:
        return True, False, f"ping 探测异常（{e}）"
    return True, True, ""


# ---------------------------------------------------------------------------
# mitmproxy CA 自动安装
# 【2026-09-06 雷电14】全新模拟器系统库没有 mitmproxy CA → 360 渠道服 SDK 严格
# 校验证书 → FGO HTTPS 走代理握手失败 → 弹"连接失败，请检查网络"。
# 修复：root 下把 CA 写入系统证书库（Android 14 双路径：APEX + legacy）。
# ---------------------------------------------------------------------------
def _der_children(data):
    """迷你 DER 解析：返回 [(tag, content, start, end)]，start/end 为元素全区间。"""
    i, out = 0, []
    while i < len(data):
        s = i
        tag = data[i]; i += 1
        ln = data[i]; i += 1
        if ln & 0x80:
            n = ln & 0x7f
            ln = int.from_bytes(data[i:i + n], "big"); i += n
        out.append((tag, data[i:i + ln], s, i + ln))
        i += ln
    return out


def cert_subject_hash_old(pem):
    """实现 openssl `x509 -subject_hash_old`（Android CA 文件名 = <hash>.0）：
    取 subject SEQUENCE 完整 DER（含 30 头）做 MD5，前 4 字节按小端显示为 8 位 hex。"""
    der = ssl.PEM_cert_to_DER_cert(pem)
    cert_seq = _der_children(der)[0]
    inner = _der_children(cert_seq[1])
    tbs = [f for f in _der_children(inner[0][1]) if f[0] != 0xA0]
    _, _, s, e = tbs[4]  # 0=serial 1=sigAlg 2=issuer 3=validity 4=subject
    subject_der = inner[0][1][s:e]
    return "%08x.0" % struct.unpack("<I", hashlib.md5(subject_der).digest()[:4])[0]


def mitm_ca_path():
    """定位主机 mitmproxy CA（mitmdump 首次运行自动生成于 ~/.mitmproxy）。"""
    for name in ("mitmproxy-ca-cert.pem", "mitmproxy-ca-cert.cer"):
        p = Path.home() / ".mitmproxy" / name
        if p.is_file():
            return p
    return None


def emulator_has_ca(adb, serial, name):
    """模拟器系统证书库（APEX / legacy 任一）已含目标 CA？"""
    for d in ("/apex/com.android.conscrypt/cacerts", "/system/etc/security/cacerts"):
        out = adb_shell(adb, ["shell", f"ls {d}/{name}"], serial, timeout=10).strip()
        if out and "No such" not in out and "not found" not in out and "No such file" not in out:
            return True
    return False


def ensure_mitm_ca(adb, serial):
    """确保模拟器信任 mitmproxy CA；缺失则 root 写入双库。返回 (ok, msg)。"""
    ca = mitm_ca_path()
    if not ca:
        return False, "未找到主机 mitmproxy CA（~/.mitmproxy/mitmproxy-ca-cert.pem），请先跑过一次 mitmdump 生成"
    try:
        pem = ca.read_text(encoding="utf-8", errors="replace")
        name = cert_subject_hash_old(pem)
    except Exception as e:
        return False, f"计算 CA 文件名失败：{e}"
    if emulator_has_ca(adb, serial, name):
        return True, f"模拟器已信任 mitmproxy CA（{name}）"
    tmp = f"/data/local/tmp/{name}"
    try:
        subprocess.run([adb, "-s", serial, "push", str(ca), tmp],
                       capture_output=True, timeout=30)
    except Exception:
        return False, f"推送 CA 到模拟器失败（{name}）"
    # 【2026-09-07 雷电9/Android9】雷电 9 默认 adbd 非 root → su -c mount 权限不足，
    # 写 system 前先 `adb root` 把 adbd 切到 root（ro.debuggable=1 有效；失败不阻断）。
    try:
        subprocess.run([adb, "-s", serial, "root"], capture_output=True, timeout=15)
        time.sleep(2)
    except Exception:
        pass
    # remount/cp 顺序：`/`（system-as-root，雷电9/Android9+）优先于 /system（老版），
    # APEX 仅 Android 10+ 有。cp 同理先 /system legacy 库。
    script = (
        f"mount -o rw,remount / 2>/dev/null; "
        f"mount -o rw,remount /system 2>/dev/null; "
        f"mount -o rw,remount /apex/com.android.conscrypt 2>/dev/null; "
        f"cp {tmp} /system/etc/security/cacerts/{name}; "
        f"cp {tmp} /apex/com.android.conscrypt/cacerts/{name}; "
        f"chmod 644 /system/etc/security/cacerts/{name} "
        f"/apex/com.android.conscrypt/cacerts/{name} 2>/dev/null; "
        f"rm -f {tmp}"
    )
    adb_shell(adb, ["shell", "su", "-c", script], serial, timeout=30)
    if emulator_has_ca(adb, serial, name):
        return True, f"mitmproxy CA 已安装到模拟器系统证书库（{name}）"
    return False, f"CA 写入模拟器失败（{name}）：请确认模拟器已开 root（多开管理器→设置→ROOT权限）"


def adb_restart_server(adb, timeout=30):
    """重启 adb server（kill + start），解决 LDPlayer 偶发断连。"""
    try:
        subprocess.run([adb, "kill-server"], capture_output=True, timeout=10)
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.run([adb, "start-server"], capture_output=True, timeout=15)
    except Exception:
        pass


def kill_stale_mitmdump():
    """启动前清理残留的 mitmdump.exe（包括其父进程 pythonw），防止 18080 端口被旧实例占用。

    Git Bash 下 `/F /IM` 会被当作路径转换，所以用 shell=True 走 cmd 直接执行。
    """
    try:
        # 先杀所有 mitmdump 进程（递归杀子进程）
        subprocess.run(
            "taskkill /F /IM mitmdump.exe /T 2>nul",
            shell=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass
    try:
        # 顺手把残留的 pythonw 父进程也清了（脚本退出后可能没被回收）
        subprocess.run(
            "taskkill /F /IM pythonw.exe /FI \"WINDOWTITLE eq FGO*\" /T 2>nul",
            shell=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass
    # 等 taskkill 在系统层面真的回收完端口（Windows 上 taskkill 返回后进程未必立即释放端口）
    deadline = time.time() + 5
    while time.time() < deadline:
        if not port_listening(PORT):
            return
        time.sleep(0.5)


def start_mitmdump():
    """启动 mitmdump，轮询等待端口就绪；失败自动重试一次。

    返回 (proc, ok: bool, log_text: str)。
    """
    log_path = TOOLS_DIR / "mitmdump.log"

    def _try_once():
        kill_stale_mitmdump()

        try:
            logf = open(log_path, "w", encoding="utf-8")
        except Exception as e:
            return None, False, f"无法打开日志文件：{e}"
        try:
            mitmdump_cmd = _get_mitmdump_cmd()
        except FileNotFoundError as e:
            logf.close()
            return None, False, str(e)
        try:
            p = subprocess.Popen(
                # 不要 -q！静默模式让 mitmdump.log 永远为空，
                # 曾据此误判"mitmdump 0 流量"。flow_detail=1 会打印每条请求，
                # 是确认 FGO 是否真走代理的唯一可靠依据。
                mitmdump_cmd + ["-p", str(PORT), "-s", str(ADDON),
                                "--set", "flow_detail=1"],
                cwd=str(TOOLS_DIR),
                stdout=logf,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            logf.close()
            return None, False, f"Popen 失败：{e}"

        # 轮询等待端口监听（最多 12 秒，冷启动 Windows 上偶尔慢）
        deadline = time.time() + 12
        while time.time() < deadline:
            if p.poll() is not None:
                # 进程已退出
                logf.close()
                try:
                    log_text = log_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    log_text = ""
                return p, False, log_text
            if port_listening(PORT):
                logf.close()
                return p, True, ""
            time.sleep(0.5)

        # 12 秒仍没监听 → 强杀并返回失败
        try:
            p.terminate()
            try:
                p.wait(timeout=2)
            except Exception:
                p.kill()
        except Exception:
            pass
        logf.close()
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = ""
        return p, False, log_text

    proc, ok, log_text = _try_once()
    if ok:
        return proc, True, ""
    print(f"[4/5] 首次启动失败，重试一次...")
    proc2, ok2, log_text2 = _try_once()
    if ok2:
        return proc2, True, ""
    return proc2, False, log_text2 or log_text


def wait_for_capture(check_fn, popup, signal_file, proxy_str, port,
                     timeout=600, title="FGO 一键抓包"):
    """复用 main() 已经启动的 popup 进程等待抓包数据，不再 spawn 第二个弹窗。

    关闭逻辑：
      • 检测到 toplogin 数据 → 写信号文件 → 弹窗自动关闭 → 返回 (cancelled=False, result=...)
      • 用户主动关闭弹窗 → cancelled=True
      • 超时 → cancelled=False, result=None（让上层弹"未检测到"错误窗）
      • Ctrl+C → cancelled=True
    """
    state = {"done": False, "cancelled": False, "result": None}
    stop_event = threading.Event()

    def worker():
        deadline = time.time() + timeout
        while not stop_event.is_set():
            if time.time() > deadline:
                return
            try:
                r = check_fn()
                if r:
                    state["result"] = r
                    state["done"] = True
                    return
            except Exception:
                pass
            time.sleep(3)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    print(f"\n=== {title} 等待抓包数据（复用前置弹窗） ===")
    print("（弹窗会持续显示等待秒数；关掉弹窗 = 立即取消）")
    start = time.time()
    try:
        while not state["done"] and not state["cancelled"]:
            elapsed = int(time.time() - start)
            # 持续更新弹窗文案：让用户看到后台真在轮询，不会误以为"卡死"
            try:
                tick_msg = (
                    f"✓ 抓包环境已就绪\n"
                    f"（代理 {proxy_str}）\n\n"
                    f"👉 现在启动 FGO，登录到公告页\n"
                    f"已等待 {elapsed} 秒…"
                )
                phase_file.write_text(tick_msg, encoding="utf-8")
            except Exception:
                pass

            # 用户主动关闭弹窗 → 取消
            if popup.poll() is not None:
                if not state.get("result"):
                    state["cancelled"] = True
                    print("[!] 检测到弹窗被关闭 → 取消等待，恢复原状态")
                break

            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\n[!] 检测到 Ctrl+C → 取消等待")
        state["cancelled"] = True

    # 通知弹窗关闭（写信号 + 等其退出）
    if popup is not None:
        if popup.poll() is None:
            try:
                signal_file.write_text("done", encoding="utf-8")
                popup.wait(timeout=3)
            except Exception:
                try:
                    popup.terminate()
                except Exception:
                    pass
        else:
            try:
                popup.wait(timeout=3)
            except Exception:
                try:
                    popup.terminate()
                except Exception:
                    pass
    try:
        if signal_file.exists():
            signal_file.unlink()
    except Exception:
        pass

    stop_event.set()
    t.join(timeout=5)
    return state["cancelled"], state["result"]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def wait_boot_complete(adb, serial, timeout_sec=120):
    """等待模拟器 boot 完成。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        out = adb_shell(adb, ["getprop", "sys.boot_completed"], serial, timeout=15)
        if out.strip() == "1":
            return True
        time.sleep(5)
    return False


def main():
    """主流程。

    2026-08-31 改造：弹窗前置 + 后台准备并行
      - t≈0: 立即 spawn 弹窗，文案 = "准备中…"
      - 后台线程跑准备（adb → mitmdump → 代理），每完成一段切换弹窗文案
      - 用户看到弹窗立刻启动 FGO；mitmdump 在用户启动 FGO 期间就绪
      - 弹窗最终文案切到「请打开 FGO 登录到公告页」时 = ready
    """

    def set_phase(text):
        """更新弹窗文案（mtime 防抖由 wait_popup_win32 检测）。"""
        try:
            phase_file.write_text(text, encoding="utf-8")
        except Exception:
            pass

    print("=" * 50)
    print("FGO 一键抓包（弹窗前置·后台并行）")
    print("=" * 50)

    # ===== 临时文件 =====
    phase_file = TOOLS_DIR / ".wait_phase.tmp"
    signal_file = TOOLS_DIR / ".wait_signal.tmp"

    # 先给上一轮可能残留的弹窗发退出信号（旧弹窗每秒检查信号文件），
    # 否则屏幕上会同时出现两个弹窗，且旧弹窗可能一直挂着不退出
    try:
        signal_file.write_text("done", encoding="utf-8")
        time.sleep(1.2)
    except Exception:
        pass

    for f in (phase_file, signal_file):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

    # ===== 0. 立即弹窗（解决"等弹窗"的痛点）=====
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.isfile(pythonw):
        pythonw = "pythonw"
    set_phase("准备中…")
    try:
        popup = subprocess.Popen(
            [
                pythonw,
                str(TOOLS_DIR / "wait_popup_win32.py"),
                "-Message", "准备中…",
                "-PhaseFile", str(phase_file),
                "-SignalFile", str(signal_file),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        error(f"无法启动弹窗进程：{e}")
        return
    print("[0/5] 弹窗已立即弹出（<2s），后台并行启动准备")
    print("  你现在可以启动 FGO；脚本就绪后弹窗会提示你登录")

    # ===== 共享状态 =====
    state = {
        "ready": False,
        "error": None,
        "adb": None,
        "serial": None,
        "mitm_proc": None,
        "cancelled": False,
    }

    # atexit：任何退出路径都还原代理 + 停 mitmdump
    import atexit
    def cleanup():
        # 已交棒给后台守护（成功路径）→ 什么都不做，让 FGO 继续走代理、不断线
        if state.get("handoff_to_daemon"):
            return
        # 1) 确保弹窗进程被回收（残留会让下次运行出现两个窗口）
        try:
            if popup.poll() is None:
                try:
                    signal_file.write_text("done", encoding="utf-8")
                    popup.wait(timeout=3)
                except Exception:
                    pass
                if popup.poll() is None:
                    popup.terminate()
        except Exception:
            pass
        # 2) 还原代理
        adb, serial = state.get("adb"), state.get("serial")
        if adb and serial:
            # 2a) 先停 FGO——清代理会切断其游戏连接（"与服务器连接中断"）
            try:
                _pkg = state.get("fgo_package") or FGO_PACKAGE
                _pid = adb_shell(adb, ["shell", "pidof", _pkg], serial).strip()
                if _pid:
                    adb_shell(adb, ["shell", "am", "force-stop", _pkg], serial)
                    time.sleep(1)
            except Exception:
                pass
            # 2b) 三个代理设置全部还原（老 API + 新 API），
            # 否则残留 global_http_proxy_host/port 会让模拟器断网。
            # ⚠️ 严禁用 `:0` 清空（历史回归会令 FGO 直连 443 → 抓不到包），
            # 用 delete 彻底清除。
            adb_shell(
                adb,
                ["shell",
                 "settings delete global http_proxy; "
                 "settings delete global global_http_proxy_host; "
                 "settings delete global global_http_proxy_port"],
                serial,
            )
        p = state.get("mitm_proc")
        if p:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                pass
    atexit.register(cleanup)

    # ===== 1. 后台准备线程（与用户手动启动 FGO 完全并行）=====
    def setup_worker():
        try:
            set_phase("[1/5] 探测模拟器…")
            adb = find_adb()
            if not adb:
                state["error"] = "未找到模拟器 adb"
                set_phase("❌ 未找到模拟器 adb，请检查路径")
                return
            print(f"[1/5] adb: {adb}", flush=True)

            set_phase("[1/5] 等待模拟器连接…")
            deadline = time.time() + 120
            serial = None
            while time.time() < deadline:
                if popup.poll() is not None:
                    state["cancelled"] = True
                    return
                serial = find_serial(adb)
                if serial:
                    break
                time.sleep(2)
            if not serial:
                state["error"] = "2 分钟内未检测到模拟器"
                set_phase("❌ 2 分钟内未检测到模拟器，请启动后重试")
                return
            state["serial"] = serial
            print(f"[1/5] 已连接: {serial}", flush=True)

            set_phase("[2/5] 检查模拟器就绪状态…")
            boot_out = adb_shell(
                adb, ["getprop", "sys.boot_completed"], serial, timeout=10
            )
            if boot_out.strip() == "1":
                print("[2/5] 模拟器已就绪", flush=True)
            else:
                if not wait_boot_complete(adb, serial, timeout_sec=60):
                    print("[2/5] boot 检查超时，继续尝试", flush=True)

            # 3. FGO 包名探测：自动识别区服/渠道，装了多个时按优先级选
            fgo_package = resolve_fgo_package(adb, serial)
            state["fgo_package"] = fgo_package

            set_phase("[3/5] 清理残留代理…")
            # 先打印旧值作诊断：若上回运行残留了失效代理（旧 IP），
            # 正是 FGO"连接失败，请检查网络"的常见诱因。
            try:
                _old = adb_shell(adb, ["settings", "get", "global", "http_proxy"], serial).strip()
                if _old and _old != "null":
                    print(f"[3/5] 检测到残留代理 {_old}，正在清除…", flush=True)
            except Exception:
                pass
            # ⚠️ 严禁用 `settings put global http_proxy :0` 清空！
            # 历史回归：`:0` 会让 FGO/Unity 读老 API 拿到空代理 → 直连 443 →
            # mitmdump 0 流量 → 永远抓不到 toplogin（"抓取没反应"）。
            # 正确做法：直接 delete 三条 key，彻底清除不留危险值，[5/5] 再重设正确值。
            adb_shell(
                adb,
                ["shell",
                 "settings delete global http_proxy; "
                 "settings delete global global_http_proxy_host; "
                 "settings delete global global_http_proxy_port"],
                serial,
            )

            set_phase("[4/5] 启动抓包服务…")
            # 无条件重启：端口被旧实例占用时若沿用旧进程，旧实例的日志句柄
            # 可能已失效（曾留下僵尸 mitmdump 占着端口，之后每次运行都跳过
            # 启动，导致抓到包也看不到日志）。start_mitmdump 内部先清残留。
            proc, ok, log_text = start_mitmdump()
            if not ok:
                tail = (log_text.strip() or "（无 stdout/stderr）")[-800:]
                state["error"] = f"抓包服务未能监听端口 {PORT}：\n{tail}"
                set_phase(f"❌ 抓包服务启动失败（端口 {PORT}）")
                return
            state["mitm_proc"] = proc
            print(f"[4/5] 抓包服务已启动（端口 {PORT}）", flush=True)

            # 【2026-09-06 雷电14/Android 14】全新模拟器系统库没有 mitmproxy CA →
            # 360 渠道服 SDK 严格校验 → FGO HTTPS 走代理握手失败 → "连接失败"。
            # 此时 mitmdump 已跑过一次，~/.mitmproxy CA 必已生成，root 写双库（APEX+legacy）。
            ca_ok, ca_msg = ensure_mitm_ca(adb, serial)
            print(f"[4/5] CA: {ca_msg}", flush=True)
            if not ca_ok:
                state["error"] = (
                    f"模拟器未信任 mitmproxy CA：{ca_msg}\n\n"
                    f"不装 CA，FGO/360 SDK 的 HTTPS 会在代理处握手失败并报连接错误。"
                    f"请确认模拟器已开启 root 后重试。"
                )
                set_phase("❌ CA 未装成功（详见弹窗）")
                return

            set_phase("[5/5] 设置代理…")
            ip = get_lan_ip()
            if not ip:
                state["error"] = "无法获取本机局域网 IP"
                set_phase("❌ 无法获取本机 IP，请检查网络")
                return
            proxy = f"{ip}:{PORT}"
            # 关键：老 API（http_proxy）必须写成 ip:port，不能清空！
            # 历史回归 bug：曾把 http_proxy 设成 :0（空），FGO/Unity 读的正是老 API
            # → 拿到空代理 → 直连 443 → mitmdump 0 流量 → 永远抓不到 toplogin。
            # 老 API + 新 API 同时设，两种客户端都覆盖。
            # 【2026-09-06 强化】设置后立即自检（读回 + ping 主机），最多 3 轮；
            # 全部失败 → 报错退出（atexit 清代理恢复模拟器网络），
            # 绝不带死代理启动 FGO（否则 FGO 必弹"连接失败，请检查网络"）。
            state["proxy_verified"] = False
            verified = False
            last_reason = ""
            for attempt in range(1, 4):
                adb_shell(
                    adb,
                    ["shell",
                     f"settings put global http_proxy {ip}:{PORT}; "
                     f"settings put global global_http_proxy_host {ip}; "
                     f"settings put global global_http_proxy_port {PORT}"],
                    serial,
                )
                time.sleep(2)  # ConnectivityService 监听 settings 变更生效
                v_ok, i_ok, last_reason = verify_proxy_ready(adb, serial, ip, PORT)
                if v_ok:
                    verified = True
                    if i_ok:
                        print(f"[5/5] 代理自检通过（第 {attempt} 次）: {proxy}", flush=True)
                    else:
                        print(f"[5/5] 代理值已写对（第 {attempt} 次）: {proxy}", flush=True)
                        print(f"[5/5] 提示：{last_reason}", flush=True)
                    break
                print(f"[5/5] 代理自检失败（第 {attempt}/3 次）：{last_reason}", flush=True)
                if attempt < 3:
                    print("[5/5] 重启 adb 后重试…", flush=True)
                    adb_restart_server(adb)
                    time.sleep(2)
            if not verified:
                state["error"] = (
                    f"代理自检 3 次未通过，未启动 FGO（避免其报\"连接失败\"）：\n"
                    f"{last_reason}\n\n"
                    f"请检查：电脑防火墙是否放通端口 {PORT}；"
                    f"模拟器网络模式（NAT/桥接）能否访问主机 {ip}"
                )
                set_phase("❌ 代理自检失败，未启动 FGO（详见弹窗/控制台）")
                return

            state["adb"] = adb
            state["ready"] = True
            state["proxy_verified"] = True
            state["ip"] = ip  # 缓存供 wait_for_capture 复用
            ready_msg = (
                f"✓ 抓包环境已就绪\n"
                f"（代理 {ip}:{PORT}）\n\n"
                f"👉 现在启动 FGO，到登录页点【登录】"
            )
            set_phase(ready_msg)
            print(f"[5/5] 代理已设置: {proxy}")
            print("=" * 50)
        except Exception as e:
            import traceback
            traceback.print_exc()
            state["error"] = f"准备过程异常：{e}"
            try:
                set_phase(f"❌ 准备异常：{e}")
            except Exception:
                pass

    threading.Thread(target=setup_worker, daemon=True).start()

    # ===== 2. 等 ready（不再傻等 20s，弹窗在跑时显示阶段文案）=====
    prep_deadline = time.time() + 180
    popup_restarts = 0
    while not state["ready"] and not state["error"] and not state["cancelled"]:
        if popup.poll() is not None:
            try:
                sig_exists = signal_file.exists()
            except Exception:
                sig_exists = "unknown"
            rc = popup.returncode
            print(
                f"[!] 弹窗进程退出 returncode={rc} signal_file存在={sig_exists}",
                flush=True,
            )
            if rc == 0:
                # returncode=0 = 正常退出（用户手动关闭窗口）→ 尊重用户取消
                state["cancelled"] = True
                break
            # 非 0 = 弹窗异常退出（秒退/崩溃）→ 自动重启，最多 3 次。
            # 曾因窗口类名冲突、窗口创建超时导致弹窗秒退，
            # 主脚本却误判成"用户取消"，整个抓包流程被掐断。
            if popup_restarts >= 3:
                print("[!] 弹窗反复崩溃，放弃重启，按取消处理", flush=True)
                state["cancelled"] = True
                break
            popup_restarts += 1
            print(f"[!] 弹窗崩溃，自动重启（第 {popup_restarts} 次）...", flush=True)
            try:
                popup = subprocess.Popen(
                    [
                        pythonw,
                        str(TOOLS_DIR / "wait_popup_win32.py"),
                        "-Message", "准备中…",
                        "-PhaseFile", str(phase_file),
                        "-SignalFile", str(signal_file),
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                print(f"[!] 重启弹窗失败：{e}", flush=True)
                state["cancelled"] = True
                break
        if time.time() > prep_deadline:
            state["error"] = "准备超时（180s）"
            try:
                set_phase("❌ 准备超时")
            except Exception:
                pass
            break
        time.sleep(0.4)

    # 处理准备期错误/取消
    if state["error"] or state["cancelled"]:
        # 给用户 5 秒看弹窗文案上的错误
        end_t = time.time() + 5
        while time.time() < end_t:
            if popup.poll() is not None:
                break
            time.sleep(0.5)
        if popup.poll() is None:
            try:
                signal_file.write_text("done", encoding="utf-8")
                popup.wait(timeout=3)
            except Exception:
                pass
        if state["error"]:
            error(state["error"])
        else:
            print("[!] 准备期间用户取消了弹窗")
        return

    # ===== 2.5 自动启动 FGO（标准动作，无条件触发）=====
    # 关键点：FGO 已登录的状态不会重发 /login/top（客户端 session 去重），
    # 只有 force-stop 后冷启动才会走完整登录流程 → addon 才会保存 toplogin。
    # 在用户的 Quicker 工作流（模拟器关闭态启动）下：
    #   - FGO 进程在的话：force-stop + monkey 等价于"重启 FGO"（走完整登录）
    #   - FGO 进程不在：force-stop 是 noop，monkey 等价于"启动 FGO"（同样走登录）
    # 两条路径结果一致 → 必触发 /login/top → 必抓到 toplogin
    print("[*] 自动启动/重启 FGO 中...", flush=True)
    set_phase("🔄 正在启动 FGO…")
    adb2 = state.get("adb")
    serial2 = state.get("serial")
    # 用探测到的包名（可能是用户在 config.ini 指定的那个）
    fgo_pkg = state.get("fgo_package") or FGO_PACKAGE
    if adb2 and serial2:
        try:
            # 【2026-09-06 启动前守门】就绪后到真正拉起 FGO 之间，
            # 代理可能被外部清空（模拟器重连 / 系统重置 / 残留配置回写），
            # 一旦 FGO 带着失效代理启动必弹"连接失败，请检查网络"。
            # 这里在 force-stop 之前做最后一道读回校验：不匹配就重设 + 自愈。
            _pip = state.get("ip") or get_lan_ip() or ""
            _expected = f"{_pip}:{PORT}"
            _cur = adb_shell(adb2, ["settings", "get", "global", "http_proxy"], serial2).strip()
            if _cur != _expected:
                print(f"[!] 启动 FGO 前代理读回 {_cur!r} ≠ 期望 {_expected}，重新写入…", flush=True)
                adb_shell(
                    adb2,
                    ["shell",
                     f"settings put global http_proxy {_expected}; "
                     f"settings put global global_http_proxy_host {_pip}; "
                     f"settings put global global_http_proxy_port {PORT}"],
                    serial2,
                )
                time.sleep(2)
                _v_ok, _i_ok, _reason = verify_proxy_ready(adb2, serial2, _pip, PORT)
                if _v_ok:
                    print("[✓] 启动 FGO 前代理已自愈"
                          + ("" if _i_ok else f"（{_reason}）"), flush=True)
                else:
                    print(f"[!] 启动前代理自愈失败：{_reason}（FGO 可能报连接失败，检查防火墙/{_pip} 可达性）", flush=True)
            else:
                print(f"[✓] 启动 FGO 前代理校验通过: {_expected}", flush=True)
            # 强制停止（幂等：未运行时 noop）
            adb_shell(adb2, ["shell", "am", "force-stop", fgo_pkg], serial2)
            time.sleep(2)
            # launcher activity 自动查询（各区服/渠道入口不同，不能硬编码）：
            # cmd package resolve-activity --brief <pkg> 会返回 pkg/xxx.Activity
            launcher = resolve_launcher(adb2, serial2, fgo_pkg)
            if not launcher:
                # 兜底：老版本 Android 可能不支持 resolve-activity，退回 monkey
                print("[!] resolve-activity 失败，退回 monkey 启动", flush=True)
                adb_shell(
                    adb2,
                    ["shell", "monkey", "-p", fgo_pkg,
                     "-c", "android.intent.category.LAUNCHER", "1"],
                    serial2,
                    timeout=30,
                )
                start_out = "(monkey)"
            else:
                print(f"[*] launcher: {launcher}", flush=True)
                start_out = adb_shell(
                    adb2,
                    ["shell", "am", "start", "-n", launcher],
                    serial2,
                    timeout=15,
                )
            print(f"[*] am start 输出: {start_out.strip()[:200]}", flush=True)
            # 等 2 秒再查一下，确认进程真起来了
            time.sleep(2)
            pidof_after = adb_shell(adb2, ["shell", "pidof", fgo_pkg], serial2).strip()
            if pidof_after:
                print(f"[*] FGO 进程已起来: PID={pidof_after}", flush=True)
            else:
                print("[!] am start 没拉起 FGO 进程，可能 splash activity 名称错", flush=True)
            set_phase(
                f"🔄 FGO 已自动启动\n"
                f"（代理 {state.get('ip','?')}:{PORT}）\n\n"
                f"👉 等待 FGO 进入登录页，点【登录】"
            )
            print("[*] FGO 已自动启动，等待登录页", flush=True)
        except Exception as e:
            print(f"[!] 自动启动 FGO 异常：{e}", flush=True)
            set_phase(f"⚠️ 自动启动 FGO 失败：{e}\n请在模拟器手动启动 FGO")
    else:
        set_phase("⚠️ adb 状态异常，未自动启动 FGO\n请在模拟器手动启动")
    # 给 FGO 几秒时间开始启动（用户操作无需等这个 sleep）
    time.sleep(3)

    # ===== 2.6 自动点击登录（默认开启，config.ini 可关）=====
    tap_cfg = load_autotap_config()
    tap_stop = {"done": False}
    if tap_cfg["auto_tap"] and adb2 and serial2:
        threading.Thread(
            target=auto_tap_worker,
            args=(adb2, serial2, tap_cfg["tap_interval"],
                  tap_cfg["tap_timeout"], tap_stop),
            daemon=True,
        ).start()

    # ===== 3. 抓包轮询（复用前置弹窗，绝不再开第二个）=====
    print()
    if tap_cfg["auto_tap"]:
        print("✓ 脚本已启动 FGO，并会自动点击屏幕中央帮你点【登录】。")
        print(f"  每 {tap_cfg['tap_interval']}s 点一次，{tap_cfg['tap_timeout']}s 内没抓到就自动结束。")
    else:
        print("✓ 脚本会自动启动 FGO；等你到登录页点【登录】即可。")
    print("  检测到登录数据后自动完成，cmd 窗口显示实时进度。")
    print()

    existing = set()
    if TOPLOGIN_DIR.exists():
        existing = {f.name for f in TOPLOGIN_DIR.glob("toplogin_*.json")}

    def check_new_file():
        if not TOPLOGIN_DIR.exists():
            return None
        new = [f for f in TOPLOGIN_DIR.glob("toplogin_*.json")
               if f.name not in existing]
        if not new:
            return None
        new.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        candidate = new[0]
        if candidate.stat().st_size < 1 * 1024 * 1024:
            return None
        tap_stop["done"] = True  # 抓到了 → 停掉自动点击
        return candidate

    # 复用 main() 前置启动的 popup（不再开第二个弹窗）
    proxy_ip = state.get("ip") or get_lan_ip() or "?"
    proxy_str = f"{proxy_ip}:{PORT}"
    # 自动点击开启时，超时=点击窗口（没抓到就结束脚本，不再干等 600s）
    wait_timeout = tap_cfg["tap_timeout"] if tap_cfg["auto_tap"] else 600
    cancelled, found = wait_for_capture(
        check_new_file, popup, signal_file,
        proxy_str=proxy_str, port=PORT, timeout=wait_timeout,
    )
    tap_stop["done"] = True  # 无论成功/超时，都停掉自动点击

    # ===== 4. 清理 + 结果 =====
    # 【2026-09-07 优雅方案】成功路径：不删代理、不杀 mitmdump、不动 FGO，
    # 改由 _cleanup_daemon.py 在后台等 FGO 退出后再清理 → FGO 全程无感、不断线、不重启。
    _adb = state.get("adb")
    _serial = state.get("serial")
    _pkg = state.get("fgo_package") or FGO_PACKAGE

    def _stop_fgo_and_clear_proxy():
        """停 FGO 再清代理（仅用于失败/取消路径——此时 FGO 没在正常游玩）。"""
        if not (_adb and _serial):
            return
        try:
            _pid = adb_shell(_adb, ["shell", "pidof", _pkg], _serial).strip()
            if _pid:
                adb_shell(_adb, ["shell", "am", "force-stop", _pkg], _serial)
                time.sleep(2)
        except Exception:
            pass
        # ⚠️ 严禁用 `:0` 清空（历史回归令 FGO 直连 443）→ 用 delete 彻底清除
        adb_shell(
            _adb,
            ["shell",
             "settings delete global http_proxy; "
             "settings delete global global_http_proxy_host; "
             "settings delete global global_http_proxy_port"],
            _serial,
        )

    if cancelled:
        _stop_fgo_and_clear_proxy()
        warn(
            "已取消等待。\n\n代理已自动还原。\n\n"
            "提示：FGO 需要登录到【地球仪/公告页】才会触发抓包，\n"
            "如果当前直接在游戏中，需要在模拟器内\n"
            "【菜单 → 设置 → 重新登录】回到登录界面重新登录。"
        )
        return

    if not found:
        _stop_fgo_and_clear_proxy()
        error(
            "未检测到新的抓包数据。\n\n"
            "可能原因：\n"
            "  1. FGO 没有登录到公告页（需重新登录触发）\n"
            "  2. 代理未生效（见弹窗提示）\n"
            "  3. FGO 报错未连接成功\n\n"
            "请检查后重新运行本工具。\n"
            "（代理已自动还原）"
        )
        return

    # ===== 5. 成功：FGO 保持在线，清理交给后台守护 =====
    size_mb = found.stat().st_size / 1024 / 1024

    copied = False
    try:
        with open(found, "rb") as f:
            content_bytes = f.read()
        proc = subprocess.run(
            ["clip"],
            input=content_bytes,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        copied = (proc.returncode == 0)
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["explorer", str(TOPLOGIN_DIR)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass

    # 拉起清理守护：等 FGO 退出后自动清代理 + 停 mitmdump。
    # 这样 FGO 全程无感（代理/mitmdump 都还在），不会因清代理被切断连接。
    try:
        daemon_py = TOOLS_DIR / "_cleanup_daemon.py"
        if daemon_py.is_file() and _adb and _serial:
            # 先清掉旧守护，避免多次抓包叠加多个守护
            subprocess.run(
                'wmic process where "name like \'%%python%%\' and commandline '
                'like \'%%_cleanup_daemon.py%%\'" delete 2>nul',
                shell=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            subprocess.Popen(
                [pythonw, str(daemon_py), _adb, _serial, _pkg, str(PORT)],
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                ),
            )
            print("[*] 清理守护已启动：FGO 退出后自动还原代理", flush=True)
            # 交棒：主脚本退出时 atexit 不再清代理/停 mitmdump（避免切断 FGO 连接）
            state["handoff_to_daemon"] = True
    except Exception as e:
        print(f"[!] 清理守护启动失败（可手动清代理）：{e}", flush=True)

    if copied:
        hint = (
            "✅ 已自动复制到剪贴板！\n\n"
            "在 Chaldea 里：\n"
            "  1. 进入【导入 → Https抓包】\n"
            "  2. 选好要导入的账号\n"
            "  3. 点击【导入】按钮 → 选【从剪贴板】即可"
        )
    else:
        hint = (
            "已自动打开文件所在文件夹。\n\n"
            "在 Chaldea 里：\n"
            "  1. 进入【导入 → Https抓包】\n"
            "  2. 选好要导入的账号\n"
            "  3. 点击【导入】按钮 → 选【从文件】→ 选择这个 json"
        )

    info(
        f"抓包成功！\n\n"
        f"文件：{found.name}\n"
        f"大小：{size_mb:.1f} MB\n\n"
        f"{hint}\n\n"
        f"✅ FGO 保持在线（未中断、未重启）。\n"
        f"退出 FGO 后代理会自动还原；\n"
        f"下次运行本工具也会自动清理。"
    )


if __name__ == "__main__":
    main()
