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

【P1-9 防重入】启动时先按命令行匹配杀掉同 serial 的旧实例。
【P1-5】清代理统一复用 auto_capture.clear_proxy；停 mitmdump 按端口 PID 精确清理。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_capture as ac


def _kill_old_instances(serial):
    """杀掉命令行同时含 _cleanup_daemon.py 与本 serial 的旧守护进程。"""
    try:
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*_cleanup_daemon.py*' -and "
            " $_.CommandLine -like '*" + serial + "*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass


def main():
    if len(sys.argv) < 5:
        return
    adb, serial, pkg, port = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    _kill_old_instances(serial)

    # 最多守护 8 小时，避免用户忘了关 FGO 时永久残留
    deadline = time.time() + 8 * 3600
    while time.time() < deadline:
        pid = ac.adb_shell(adb, ["pidof", pkg], serial)
        if not pid:
            # FGO 已退出 → 执行清理
            break
        time.sleep(10)

    # 1) 清代理三条（⚠️ 严禁 `:0` 清空——历史回归令 FGO 直连 443）
    ac.clear_proxy(adb, serial)

    # 2) 停 mitmdump（netstat 按端口 PID 精确清理，含其子进程）
    ac._kill_by_port(port)


if __name__ == "__main__":
    main()
