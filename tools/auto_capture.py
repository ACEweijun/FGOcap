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
import os
import re
import socket
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
      2) PATH 中的 mitmdump（pip install mitmproxy 后直接可用）
      3) python -m mitmdump（模块方式，兜底）
    """
    local = TOOLS_DIR / "mitmproxy-12.2.3" / "mitmdump.exe"
    if local.is_file():
        return [str(local)]
    import shutil
    for name in ("mitmdump.exe", "mitmdump"):
        p = shutil.which(name)
        if p:
            return [p]
    return [sys.executable, "-m", "mitmdump"]


MITMDUMP_CMD = resolve_mitmdump()

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
FGO_PACKAGE = "com.bilibili.fgo.qihoo"   # 360 渠道服

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
    for p in LDPLAYER_PATHS:
        if os.path.isfile(p):
            return p
    # 兜底：在 PATH 里找
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
            p = subprocess.Popen(
                # 不要 -q！静默模式让 mitmdump.log 永远为空，
                # 曾据此误判"mitmdump 0 流量"。flow_detail=1 会打印每条请求，
                # 是确认 FGO 是否真走代理的唯一可靠依据。
                MITMDUMP_CMD + ["-p", str(PORT), "-s", str(ADDON),
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
            try:
                # 三个代理设置全部还原（老 API + 新 API），
                # 否则残留 global_http_proxy_host/port 会让模拟器断网
                adb_shell(
                    adb,
                    ["shell",
                     "settings put global http_proxy :0; "
                     "settings delete global global_http_proxy_host; "
                     "settings delete global global_http_proxy_port"],
                    serial,
                )
            except Exception:
                pass
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

            # 3. FGO 检查：只查这一个包（全量 pm list packages 在模拟器上要几十秒）
            pkg_out = adb_shell(
                adb, ["pm", "list", "packages", FGO_PACKAGE], serial, timeout=20
            )
            if FGO_PACKAGE not in pkg_out:
                print(f"[3/5] 未检测到 FGO 360 渠道服，继续...", flush=True)

            set_phase("[3/5] 清理残留代理…")
            # B 优化：3 条 settings 命令合并为 1 条 adb shell 调用
            adb_shell(
                adb,
                ["shell",
                 "settings put global http_proxy :0; "
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
            adb_shell(
                adb,
                ["shell",
                 f"settings put global http_proxy {ip}:{PORT}; "
                 f"settings put global global_http_proxy_host {ip}; "
                 f"settings put global global_http_proxy_port {PORT}"],
                serial,
            )
            time.sleep(2)  # ConnectivityService 监听 settings 变更生效

            # 状态校验（不阻断，让数据抓到为准；adb 偶尔读回空不致命）
            cur = adb_shell(adb, ["settings", "get", "global", "http_proxy"], serial)
            new_host = adb_shell(adb, ["settings", "get", "global",
                                        "global_http_proxy_host"], serial)
            new_port = adb_shell(adb, ["settings", "get", "global",
                                        "global_http_proxy_port"], serial)
            new_ok = (new_host == ip and new_port == str(PORT))
            if cur != proxy and not new_ok:
                print("[5/5] 代理读回不匹配，重连 adb 重试一次", flush=True)
                adb_restart_server(adb)
                time.sleep(2)
                adb_shell(
                    adb,
                    ["shell",
                     f"settings put global http_proxy {ip}:{PORT}; "
                     f"settings put global global_http_proxy_host {ip}; "
                     f"settings put global global_http_proxy_port {PORT}"],
                    serial,
                )
                time.sleep(2)

            state["adb"] = adb
            state["ready"] = True
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
    if adb2 and serial2:
        try:
            # 强制停止（幂等：未运行时 noop）
            adb_shell(adb2, ["shell", "am", "force-stop", FGO_PACKAGE], serial2)
            time.sleep(2)
            # 用 am start 启动 FGO 真 launcher activity（SplashActivity）
            # 之前用 monkey 在 LDPlayer 上不可靠（只 echo 参数不实际启动）
            launcher = f"{FGO_PACKAGE}/com.bilibili.fatego.SplashActivity"
            start_out = adb_shell(
                adb2,
                ["shell", "am", "start", "-n", launcher],
                serial2,
                timeout=15,
            )
            print(f"[*] am start 输出: {start_out.strip()[:200]}", flush=True)
            # 等 2 秒再查一下，确认进程真起来了
            time.sleep(2)
            pidof_after = adb_shell(adb2, ["shell", "pidof", FGO_PACKAGE], serial2).strip()
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

    # ===== 3. 抓包轮询（复用前置弹窗，绝不再开第二个）=====
    print()
    print("✓ 脚本会自动启动 FGO；等你到登录页点【登录】即可。")
    print("  脚本会在后台静默等待，检测到登录数据后自动完成。")
    print("  等待期间无需任何操作，cmd 窗口显示实时进度。")
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
        return candidate

    # 复用 main() 前置启动的 popup（不再开第二个弹窗）
    proxy_ip = state.get("ip") or get_lan_ip() or "?"
    proxy_str = f"{proxy_ip}:{PORT}"
    cancelled, found = wait_for_capture(
        check_new_file, popup, signal_file,
        proxy_str=proxy_str, port=PORT, timeout=600,
    )

    # ===== 4. 清理 + 结果（保留原逻辑）=====
    if state.get("adb") and state.get("serial"):
        adb_shell(
            state["adb"],
            ["shell",
             "settings put global http_proxy :0; "
             "settings delete global global_http_proxy_host; "
             "settings delete global global_http_proxy_port"],
            state["serial"],
        )

    if cancelled:
        warn(
            "已取消等待。\n\n代理已自动还原。\n\n"
            "提示：FGO 需要登录到【地球仪/公告页】才会触发抓包，\n"
            "如果当前直接在游戏中，需要在模拟器内\n"
            "【菜单 → 设置 → 重新登录】回到登录界面重新登录。"
        )
        return

    if not found:
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

    # ===== 5. 成功 =====
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
        f"代理已自动还原，抓包服务已停止。"
    )


if __name__ == "__main__":
    main()
