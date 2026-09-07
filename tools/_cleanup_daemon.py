# -*- coding: utf-8 -*-
"""FGO 抓包清理守护（detached 后台运行）

使命：抓包成功后主脚本直接退出（**不删代理、不杀 mitmdump、不动 FGO**），
由本守护在后台等 FGO 退出，再清代理 + 停 mitmdump。

为什么需要它：
  抓包时代理生效，FGO 游戏中保持长连接。如果在 FGO 还活着时删代理，
  ConnectivityService 刷新会切断 FGO 活动连接 → 弹"与服务器连接中断"。
  故改为"等 FGO 自己退出后再清理"——FGO 全程无感、不重启、不断线。

用法（由 auto_capture.py 自动拉起，无需手动）：
  pythonw _cleanup_daemon.py <adbPath> <serial> <fgoPkg> <port>
"""
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000


def _adb_shell(adb, serial, cmd, timeout=30):
    try:
        r = subprocess.run([adb, "-s", serial, "shell", cmd],
                           capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def main():
    if len(sys.argv) < 5:
        return
    adb, serial, pkg, port = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

    # 最多守护 8 小时，避免用户忘了关 FGO 时永久残留
    deadline = time.time() + 8 * 3600
    while time.time() < deadline:
        pid = _adb_shell(adb, serial, "pidof " + pkg)
        if not pid:
            # FGO 已退出 → 执行清理
            break
        time.sleep(10)

    # 1) 清代理三条（⚠️ 严禁 `:0` 清空——历史回归令 FGO 直连 443）
    _adb_shell(adb, serial,
               "settings delete global http_proxy; "
               "settings delete global global_http_proxy_host; "
               "settings delete global global_http_proxy_port")

    # 2) 停 mitmdump（含其父进程/子进程）
    try:
        subprocess.run("taskkill /F /IM mitmdump.exe /T 2>nul",
                       shell=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


if __name__ == "__main__":
    main()
