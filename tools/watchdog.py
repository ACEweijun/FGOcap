# -*- coding: utf-8 -*-
"""
FGO 守护模式（Watchdog）
=========================
常驻后台，监控模拟器中 FGO 客户端的启动，全自动完成抓包：

  1. 检测到 FGO 在前台运行 → 自动启动 mitmdump + 设置代理
  2. 用户正常登录 FGO 到公告页（无需任何额外操作）
  3. 检测到新 toplogin json → 自动复制到剪贴板
  4. 弹窗告知 → 清理环境 → 回到监控状态

用法：双击「守护抓包.bat」启动。关闭窗口或 Ctrl+C 退出。
"""

import subprocess
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

# 复用 auto_capture.py 的工具函数
from auto_capture import (
    TOOLS_DIR, MITMDUMP, ADDON, TOPLOGIN_DIR, PORT,
    FGO_PACKAGE,
    find_adb, find_serial, adb_shell, port_listening, get_lan_ip,
    msg, info, warn, error,
)

CHECK_INTERVAL = 3        # 监控轮询间隔（秒）
STABLE_AFTER = 2          # FGO 前台需连续检测到几次才触发（防误触发）
WAIT_TIMEOUT = 600        # 等待抓包数据超时（秒）
COOLDOWN = 60             # 会话结束后冷却时间（秒），防止重复触发


def is_fgo_foreground(adb, serial):
    """检测 FGO 是否在前台运行。"""
    out = adb_shell(adb, ["dumpsys", "activity", "activities"], serial, timeout=15)
    if not out:
        return False
    for line in out.splitlines():
        if "mResumedActivity" in line:
            return FGO_PACKAGE in line
    return False


def setup_capture(adb, serial):
    """准备抓包环境：启动 mitmdump + 设置代理。返回脚本启动的进程（或 None=复用已有）。"""
    started = None
    if not port_listening(PORT):
        print("[守护] 启动抓包服务...")
        try:
            with open(TOOLS_DIR / "mitmdump.log", "w", encoding="utf-8") as logf:
                started = subprocess.Popen(
                    [str(MITMDUMP), "-q", "-p", str(PORT), "-s", str(ADDON)],
                    cwd=str(TOOLS_DIR),
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            time.sleep(4)
        except Exception:
            started = None
    ip = get_lan_ip()
    if not ip:
        warn("无法获取电脑局域网 IP，抓包可能失败。")
        return started
    proxy = f"{ip}:{PORT}"
    adb_shell(adb, ["settings", "put", "global", "http_proxy", proxy], serial)
    time.sleep(1)
    return started


def cleanup(adb, serial, started):
    """还原代理 + 停止自己启动的 mitmdump。"""
    try:
        adb_shell(adb, ["settings", "put", "global", "http_proxy", ":0"], serial)
    except Exception:
        pass
    if started is not None:
        try:
            started.terminate()
            started.wait(timeout=5)
        except Exception:
            pass


def copy_to_clipboard(path):
    """把文件内容写入剪贴板（用 Windows 自带 clip 命令）。"""
    try:
        with open(path, "rb") as f:
            content = f.read()
        proc = subprocess.run(
            ["clip"], input=content, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except Exception:
        return False


def main():
    print("=" * 56)
    print("FGO 守护模式（Watchdog）")
    print("监控模拟器 FGO 启动 → 自动抓包 → 自动复制剪贴板")
    print("=" * 56)

    info("FGO 守护模式已启动\n\n"
         "后台持续监控中：\n"
         "  1. 检测到模拟器里打开 FGO → 自动准备抓包\n"
         "  2. 你正常登录到【地球仪/公告页】即可\n"
         "  3. 抓到数据后自动复制到剪贴板\n\n"
         "本窗口请保持打开（可最小化）。\n"
         "点击【确定】开始监控")

    adb = find_adb()
    if not adb:
        error("未找到模拟器 adb！守护模式无法运行。\n\n"
              "请确认雷电模拟器已安装。")
        return

    # 记录已有文件，只认新文件
    existing = set()
    if TOPLOGIN_DIR.exists():
        existing = {f.name for f in TOPLOGIN_DIR.glob("toplogin_*.json")}

    stable_count = 0
    capture_active = False
    started_proc = None
    wait_start = 0
    cooldown_until = 0

    print(f"[守护] 监控中（每 {CHECK_INTERVAL} 秒检测）  Ctrl+C 退出")
    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            serial = find_serial(adb)
            if not serial:
                stable_count = 0
                print("[守护] 等待模拟器连接...")
                continue

            foreground = is_fgo_foreground(adb, serial)

            # 触发抓包会话
            if foreground and not capture_active and time.time() > cooldown_until:
                stable_count += 1
                if stable_count >= STABLE_AFTER:
                    print("\n[守护] 检测到 FGO 在前台！自动准备抓包环境...")
                    stable_count = 0
                    capture_active = True
                    wait_start = time.time()
                    started_proc = setup_capture(adb, serial)
                    print("[守护] 环境已就绪，等待登录数据...（10 分钟内有效）")
            elif not foreground:
                stable_count = 0

            # 等待抓包结果
            if capture_active:
                found = None
                if TOPLOGIN_DIR.exists():
                    new = [f for f in TOPLOGIN_DIR.glob("toplogin_*.json")
                           if f.name not in existing]
                    if new:
                        new.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                        if new[0].stat().st_size >= 1 * 1024 * 1024:
                            found = new[0]

                if found:
                    existing.add(found.name)
                    cleanup(adb, serial, started_proc)
                    capture_active = False
                    started_proc = None
                    cooldown_until = time.time() + COOLDOWN

                    copied = copy_to_clipboard(found)
                    size_mb = found.stat().st_size / 1024 / 1024
                    if copied:
                        info(f"抓包成功，已自动复制到剪贴板！\n\n"
                             f"文件：{found.name}\n"
                             f"大小：{size_mb:.1f} MB\n\n"
                             f"在 Chaldea 里：\n"
                             f"  导入 \u2192 Https\u6293\u5305 \u2192 \u9009\u8d26\u53f7\n"
                             f"  \u2192 \u70b9\u3010\u5bfc\u5165\u3011\u2192\u3010\u4ece\u526a\u8d34\u677f\u3011\n\n"
                             f"\u73af\u5883\u5df2\u6e05\u7406\uff0c\u5b88\u62a4\u6a21\u5f0f\u7ee7\u7eed\u76d1\u63a7\u4e2d\u3002")
                    else:
                        info(f"抓包成功！\n\n"
                             f"文件：{found.name}\n"
                             f"大小：{size_mb:.1f} MB\n\n"
                             f"（剪贴板复制失败，请从文件夹手动导入）\n"
                             f"已打开文件夹。")
                        try:
                            subprocess.Popen(
                                ["explorer", str(TOPLOGIN_DIR)],
                                creationflags=subprocess.CREATE_NO_WINDOW,
                            )
                        except Exception:
                            pass
                    print("[守护] 本次抓包完成，进入冷却期，继续监控...")

                elif time.time() - wait_start > WAIT_TIMEOUT:
                    print("[守护] 10 分钟未检测到登录数据，结束本次会话")
                    cleanup(adb, serial, started_proc)
                    capture_active = False
                    started_proc = None
                    cooldown_until = time.time() + COOLDOWN
                    warn("10 分钟内未检测到登录数据。\n\n"
                         "请确认 FGO 是否登录到了【地球仪/公告页】？\n"
                         "如果已经在游戏中，需在 FGO 里\n"
                         "【菜单 \u2192 设置 \u2192 重新登录】触发登录请求。\n\n"
                         "\u73af\u5883\u5df2\u6e05\u7406\uff0c\u5b88\u62a4\u6a21\u5f0f\u7ee7\u7eed\u76d1\u63a7\u4e2d\u3002")
                else:
                    elapsed = int(time.time() - wait_start)
                    print(f"[守护] 等待抓包数据... {elapsed}s ", end="\r", flush=True)

    except KeyboardInterrupt:
        print("\n[守护] 收到退出信号，清理环境...")
        if capture_active:
            serial = find_serial(adb)
            cleanup(adb, serial, started_proc)
        print("[守护] 已退出")


if __name__ == "__main__":
    main()
