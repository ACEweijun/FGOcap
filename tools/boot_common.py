# -*- coding: utf-8 -*-
"""
职责：两个开机入口共用的前置与收尾（唯一实现，避免 A / B 各写一份）。

    A = 一键抓包.bat → auto_capture.py      含抓包
    B = 一键开机-不抓包.bat → start_plain.py  不含抓包

对外函数：
    launch_emulator(adb, index=0)              发出启动模拟器指令
    ensure_emulator(adb, index=0) -> serial     B 用：启动 + 等上线 + 等 boot
    detect_and_clear_proxy(adb, serial)         两个入口共用：检测残留 → 清除 → 读回校验
    start_fgo(adb, serial) -> bool              B 用：冷启动 FGO
    spawn_daemon_and_wait(adb, serial, pkg)     两个入口共用：拉起守护 + 挂住 CMD 窗口

约束：清代理必须在模拟器在线时完成；关模拟器只能由守护在最后执行。
本模块由入口脚本局部导入，避免 auto_capture 以 __main__ 运行时出现循环导入。
"""

import os
import subprocess
import sys
import time
from pathlib import Path

# A 以 `python auto_capture.py` 直接运行时模块名是 __main__；直接复用它，
# 避免 import 出第二份 auto_capture 实例（常量与 helper 会有两份）。
_main = sys.modules.get("__main__")
if _main is not None and os.path.basename(
        getattr(_main, "__file__", "")) == "auto_capture.py":
    ac = _main
else:
    import auto_capture as ac

PROXY_KEYS = ("http_proxy", "global_http_proxy_host", "global_http_proxy_port")
MUTEX_NAME = "FGOcap_SingleInstance_Mutex"
DAEMON = Path(__file__).resolve().parent / "_cleanup_daemon.py"


def launch_emulator(adb, index=0):
    """ldconsole launch --index <n>。返回 False 只表示指令没发出，不阻断后续等待。"""
    ldc = Path(adb).resolve().parent / "ldconsole.exe"
    if not ldc.is_file():
        print(f"[!] 未找到 {ldc}，请手动启动模拟器", flush=True)
        return False
    try:
        subprocess.run([str(ldc), "launch", "--index", str(index)], timeout=30)
        print(f"[*] 已发出启动模拟器指令（index={index}）", flush=True)
        return True
    except Exception as e:
        print(f"[!] 启动模拟器失败：{e}", flush=True)
        return False


def ensure_emulator(adb, index=0, cancel_check=None):
    """设备已在线则直接返回；否则启动模拟器，等 adb 上线与 sys.boot_completed=1。"""
    deadline = time.time() + 150
    launch_sent = False
    serial = None
    while time.time() < deadline:
        if cancel_check and cancel_check():
            return None
        serial = ac.find_serial(adb)
        if serial:
            break
        if not launch_sent:
            launch_emulator(adb, index)
            launch_sent = True
        time.sleep(2)
    if not serial:
        return None
    print(f"[*] 设备已连接：{serial}", flush=True)
    if not ac.wait_boot_complete(adb, serial, timeout_sec=120):
        print("[!] boot_completed 等待超时，继续尝试", flush=True)
    return serial


def _read_proxy_keys(adb, serial):
    out = {}
    for key in PROXY_KEYS:
        try:
            out[key] = (ac.adb_shell(
                adb, ["settings", "get", "global", key], serial, timeout=15
            ) or "").strip()
        except Exception:
            out[key] = ""
    return out


def detect_and_clear_proxy(adb, serial):
    """检测三条 key → 清除 → 逐条读回校验（最多 3 轮）。只有返回 True 才允许启动 FGO。"""
    dirty = {k: v for k, v in _read_proxy_keys(adb, serial).items()
             if v and v != "null"}
    if dirty:
        print(f"[--] 检测到残留代理 {dirty}，正在清除…", flush=True)
    else:
        print("[--] 无残留代理", flush=True)

    for attempt in range(1, 4):
        ac.clear_proxy(adb, serial)
        left = {k: v for k, v in _read_proxy_keys(adb, serial).items()
                if v and v != "null"}
        if not left:
            print("[OK] 三条代理 key 已清除（读回确认为 null）", flush=True)
            return True
        print(f"[!] 第 {attempt}/3 轮读回仍有残留 {left}", flush=True)
        time.sleep(2)
    print("[FAIL] 代理无法清除，已中止，未启动 FGO", flush=True)
    return False


def start_fgo(adb, serial):
    """冷启动 FGO（force-stop + am start），返回 FGO 进程是否真的起来。"""
    pkg = ac.resolve_fgo_package(adb, serial)
    ac.adb_shell(adb, ["am", "force-stop", pkg], serial, timeout=15)
    time.sleep(1)
    launcher = ac.resolve_launcher(adb, serial, pkg)
    if launcher:
        ac.adb_shell(adb, ["am", "start", "-n", launcher], serial, timeout=15)
    else:
        ac.adb_shell(
            adb,
            ["monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
            serial, timeout=30,
        )
    time.sleep(2)
    pid = (ac.adb_shell(adb, ["pidof", pkg], serial) or "").strip()
    if pid:
        print(f"[OK] FGO 已启动（{pkg}，PID={pid}）", flush=True)
        return True
    print(f"[!] FGO 未起来（{pkg}），请在模拟器里手动打开", flush=True)
    return False


def spawn_daemon_and_wait(adb, serial, pkg, on_handoff=None):
    """拉起清理守护并挂住 CMD 窗口直到守护退出。

    窗口被关 = 守护自动全收；设备离线 = 守护立即收工（窗口随之关闭）。
    """
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not Path(pythonw).is_file():
        pythonw = "pythonw"
    if not DAEMON.is_file():
        print("[!] 缺少 _cleanup_daemon.py，关窗即全收不可用", flush=True)
        return -1

    proc = None
    try:
        # 守护自身 _kill_old_instances(serial) 会按 serial 清理旧实例，此处无需重复杀
        proc = subprocess.Popen(
            [pythonw, str(DAEMON), adb, serial, pkg, str(ac.PORT),
             str(os.getpid())],
            creationflags=(subprocess.DETACHED_PROCESS
                           | subprocess.CREATE_NEW_PROCESS_GROUP
                           | subprocess.CREATE_NO_WINDOW),
        )
        print("[*] 清理守护已启动", flush=True)
        # 守护秒死（pythonw 缺失 / import 失败）时不能交棒，否则代理与 mitmdump 永久残留
        time.sleep(3)
        if proc.poll() is not None:
            print(f"[!] 守护启动后立即退出（code={proc.returncode}），本窗口将自行结束",
                  flush=True)
            return proc.returncode
        if on_handoff:
            on_handoff()
    except Exception as e:
        print(f"[!] 清理守护启动失败：{e}", flush=True)
        return -1

    print("[*] 想直接收工？关掉本窗口即可：守护自动清代理、停 mitmdump、关闭模拟器",
          flush=True)
    _log_wait("主脚本开始等待清理守护")
    waited, rc = 0, None
    while rc is None:
        try:
            rc = proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            waited += 60
            print(f"[*] 仍在运行…（已等待 {waited // 60} 分钟）", flush=True)
        except Exception as e:
            rc = -1
            print(f"[!] 等待守护失败：{e}", flush=True)
    _log_wait(f"清理守护退出 code={rc}（等待 {waited}s）")

    if rc == 0:
        left = (ac.adb_shell(adb, ["settings", "get", "global", "http_proxy"],
                             serial) or "").strip()
        if left and left != "null":
            print(f"[!] 代理仍残留 {left!r}，请运行 tools/一键抓包-恢复.bat", flush=True)
        else:
            print("[✓] 环境已还原，全部完成", flush=True)
    elif rc == 2:
        print("[*] 检测到模拟器已关闭，守护已收工（mitmdump 已停止）", flush=True)
        print("[*] 代理残留由下次开机脚本自动清除", flush=True)
    else:
        print(f"[!] 清理未完成（守护退出码 {rc}）", flush=True)
        print("[!] 可运行 tools/一键抓包-恢复.bat 手动清代理 + 停 mitmdump", flush=True)
    return rc


def _log_wait(text):
    try:
        with open(ac.RUN_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [wait] {text}\n")
    except Exception:
        pass
