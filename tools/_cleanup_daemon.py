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

【2026-10-29 重构：状态机 + 验证驱动】
  1) 查询语义化：pidof 带返回码，区分「FGO 已退出」（rc=0 且输出空）
     与「设备不可达」（rc≠0：模拟器被关/崩溃/adb 抖动）。后者旧版直接
     误判为 FGO 退出 → 清理命令发不出去却报 code=0（假成功，代理永久
     残留）——现改为等设备回来（宽限期 10 分钟），超时才放弃。
  2) 清理验证化：删完代理必须读回确认 http_proxy 为空/null，非空重试 3 轮。
  3) 退出码语义化（主脚本按码给针对性提示，见 auto_capture 成功路径）：
        0 = 清理成功且已读回验证
        2 = 设备离线超过宽限期未恢复，未执行清理
        3 = 执行了清理但代理仍有残留

【P1-9 防重入】启动时先按命令行匹配杀掉同 serial 的旧实例。
【2026-10-25 修复】匹配条件排除自身 PID，否则守护启动即自杀。
【P1-5】清代理统一复用 auto_capture.clear_proxy；停 mitmdump 按端口 PID 精确清理。
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_capture as ac

# 退出码（与 auto_capture.py 成功路径的消费逻辑对应）
RC_OK = 0            # 清理成功且已读回验证
RC_OFFLINE = 2       # 设备离线超过宽限期未恢复，未执行清理
RC_FAILED = 3        # 执行了清理但代理仍有残留

OFFLINE_GRACE = 600  # 设备离线后等它回来的最长时间（秒）
OFFLINE_POLL = 30    # 设备离线期间的轮询间隔（秒）


def _log(msg):
    """写 run.log（与主脚本 [wait] 记录同一文件，带 [daemon] 前缀）。"""
    try:
        with open(ac.RUN_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [daemon] {msg}\n")
    except Exception:
        pass


def _kill_old_instances(serial):
    """杀掉命令行同时含 _cleanup_daemon.py 与本 serial 的旧守护进程。

    【2026-10-25 修复】必须排除自身 PID：本进程自己的命令行同样匹配
    `*_cleanup_daemon.py*` + serial，旧版不排除 → 守护启动后先把自己
    Stop-Process 掉 → 主脚本 wait() 秒返回、CMD 窗口抓到数据就关，
    且 handoff 已交棒 → atexit 跳过清理 → 代理/mitmdump 永久残留。
    """
    try:
        ps = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -like '*_cleanup_daemon.py*' -and "
            " $_.CommandLine -like '*" + serial + "*' -and "
            " $_.ProcessId -ne " + str(os.getpid()) + " } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass


def _probe_fgo(adb, serial, pkg):
    """查询 FGO 进程状态，返回 'running' / 'gone' / 'offline'。

    gone：命令成功执行且 pidof 输出为空 → FGO 确实已退出
    offline：adb 命令本身失败（设备离线/被关/adb 抖动）→ 不能下结论，
        旧版把它当 gone 是「假清理」的直接根因。
    """
    rc, out = ac.adb_shell(adb, ["pidof", pkg], serial, timeout=15, return_rc=True)
    if rc != 0:
        return "offline"
    return "running" if out.strip() else "gone"


def _clear_proxy_verified(adb, serial):
    """清代理三条 key 并读回校验（最多 3 轮）。

    返回 True = 已确认 http_proxy 为空/null。只删不验是旧版第二个
    静默失败点（设备在线但 delete 偶发失败时无感知）。
    """
    for attempt in range(1, 4):
        ac.clear_proxy(adb, serial)
        rc, val = ac.adb_shell(adb, ["settings", "get", "global", "http_proxy"],
                               serial, timeout=15, return_rc=True)
        val = (val or "").strip()
        if rc == 0 and val in ("", "null"):
            return True
        _log(f"代理读回校验失败（第 {attempt}/3 轮）：http_proxy={val!r} rc={rc}")
        time.sleep(2)
    return False


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 4:
        return RC_FAILED
    adb, serial, pkg, port = argv[0], argv[1], argv[2], argv[3]
    _kill_old_instances(serial)

    # 最多守护 8 小时，避免用户忘了关 FGO 时永久残留
    deadline = time.time() + 8 * 3600
    offline_since = None
    while time.time() < deadline:
        state = _probe_fgo(adb, serial, pkg)
        if state == "running":
            offline_since = None
            time.sleep(10)
            continue
        if state == "offline":
            # 【2026-10-29】设备离线 ≠ FGO 退出：模拟器被关/崩溃/adb 抖动时
            # pidof 同样查不到，旧版误判为退出 → clear_proxy 的 adb 命令同样
            # 失败 → 清理从未发生却报 code=0。现等设备回来再继续判断。
            if offline_since is None:
                offline_since = time.time()
                _log(f"设备离线/不可达，等待模拟器恢复（宽限期 {OFFLINE_GRACE} 秒）")
            elif time.time() - offline_since > OFFLINE_GRACE:
                _log(f"设备离线超过宽限期（{OFFLINE_GRACE} 秒）未恢复，放弃清理"
                     "（代理可能残留在模拟器中，开模拟器后运行 一键抓包-恢复.bat）")
                return RC_OFFLINE
            time.sleep(OFFLINE_POLL)
            continue
        break  # gone：设备在线且 FGO 已退出 → 执行清理

    # 1) 清代理三条 + 读回校验（失败重试 3 轮）
    if not _clear_proxy_verified(adb, serial):
        _log("代理清除失败：http_proxy 仍有残留，请手动运行 一键抓包-恢复.bat")
        # mitmdump 是 PC 侧进程（taskkill 按端口），与设备在否无关，照杀
        ac._kill_by_port(port)
        return RC_FAILED

    # 2) 停 mitmdump（netstat 按端口 PID 精确清理，含其子进程）
    ac._kill_by_port(port)
    _log("清理完成：代理已删除（读回确认 null）、mitmdump 已停止")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
