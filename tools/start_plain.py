# -*- coding: utf-8 -*-
"""
职责：不含抓包的开机入口（Quicker 动作 B，由「一键开机-不抓包.bat」调用）。

流程：启动模拟器 → 检测并清除残留代理 → 停残留 mitmdump → 冷启动 FGO
      → 自动连点到公告/通知页（与入口 A 共用 auto_tap_worker）
      → 拉起清理守护并挂住 CMD 窗口（关窗即全收，与含抓包入口一致）。

不设代理、不启 mitmdump、不抓 toplogin、不写剪贴板。
与 auto_capture.py 共用同名互斥体，两个开机入口不会同时运行。
"""

import ctypes
import sys
import threading

import auto_capture as ac
import boot_common as boot

_MUTEX = None


def main():
    global _MUTEX
    print("=" * 50)
    print("FGO 一键开机（不抓包）")
    print("=" * 50, flush=True)

    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, True, boot.MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        print("[FAIL] 已有开机流程在运行中（含抓包或不抓包）", flush=True)
        return 1

    adb = ac.find_adb()
    if not adb:
        print("[FAIL] 未找到模拟器 adb", flush=True)
        return 1

    serial = boot.ensure_emulator(adb)
    if not serial:
        print("[FAIL] 模拟器未能在限定时间内上线", flush=True)
        return 1

    if not boot.detect_and_clear_proxy(adb, serial):
        return 1

    ac.kill_stale_mitmdump()
    if ac.port_listening(ac.PORT):
        print(f"[!] 端口 {ac.PORT} 仍被占用，可能有别的程序在监听", flush=True)
    else:
        print(f"[OK] 残留 mitmdump 已停止（端口 {ac.PORT} 已释放）", flush=True)

    boot.start_fgo(adb, serial)

    tap_cfg = ac.load_autotap_config()
    tap_stop = {"done": False}
    if tap_cfg["auto_tap"]:
        threading.Thread(
            target=ac.auto_tap_worker,
            args=(adb, serial, tap_cfg["tap_interval"],
                  tap_cfg["plain_tap_duration"], tap_stop,
                  tap_cfg.get("tap_x"), tap_cfg.get("tap_y")),
            daemon=True,
        ).start()
    else:
        print("[*] 自动点击已关闭（config.ini: auto_tap=0），请自己点进游戏", flush=True)

    pkg = ac.resolve_fgo_package(adb, serial)
    rc = boot.spawn_daemon_and_wait(adb, serial, pkg)
    tap_stop["done"] = True
    # rc=2（设备离线收工）是正常收工：返回非 0 会让 bat 执行 pause 卡住窗口
    return 0 if rc in (0, 2) else rc


if __name__ == "__main__":
    sys.exit(main())
