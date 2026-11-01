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
import re
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


def _parent_alive(pid):
    """探测父进程（主脚本）是否还活着。

    【2026-11-01 关窗即全收】主脚本比守护先退出只可能是三种情况：
    用户关掉 CMD 窗口 / 任务管理器杀进程 / 崩溃——全部都意味着"该收工了"。
    Windows 实现：OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) + GetLastError；
    非 Windows 平台（测试替身）退化为 os.kill(pid, 0) 探测。
    """
    if pid is None or pid <= 0:
        return True   # 没给父 PID（旧版主脚本调用）→ 视为永远活着，保持老行为
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False   # 打不开：进程已不存在（或权限外，对本场景等价死亡）
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except AttributeError:
        # 非 Windows：os.kill(pid, 0) 存活探测
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    except Exception:
        return True   # 探测本身失败：宁可不触发全收（正常路径兜底）


def _quit_emulator(adb_path, serial):
    """关闭整个模拟器（ldconsole quit）。返回 True = 命令已发出。

    serial 形如 emulator-5554 → index = (5554-5554)//2 = 0（LDPlayer 惯例：
    5554/5556/5558… 依次对应 index 0/1/2）。ldconsole 与 adb 同目录。
    """
    try:
        m = re.match(r"emulator-(\d+)", serial)
        if not m:
            _log(f"无法从 serial {serial!r} 推断模拟器 index，跳过关模拟器")
            return False
        index = (int(m.group(1)) - 5554) // 2
        ldc = os.path.join(os.path.dirname(os.path.abspath(adb_path)),
                           "ldconsole.exe")
        if not os.path.isfile(ldc):
            _log(f"未找到 {ldc}，跳过关模拟器（请手动关闭）")
            return False
        for attempt in (1, 2):
            subprocess.run([ldc, "quit", "--index", str(index)],
                           timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(3)
            rc, _ = ac.adb_shell(adb_path, ["echo", "probe"], serial,
                                 timeout=10, return_rc=True)
            if rc != 0:
                _log(f"模拟器已关闭（ldconsole quit index={index}）")
                return True
            _log(f"模拟器仍在运行，重试 ldconsole quit（第 {attempt}/2 次）")
        _log("ldconsole quit 两次后模拟器仍在运行，请手动关闭")
        return False
    except Exception as e:
        _log(f"关模拟器异常：{e}")
        return False


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 4:
        return RC_FAILED
    adb, serial, pkg, port = argv[0], argv[1], argv[2], argv[3]
    parent_pid = int(argv[4]) if len(argv) >= 5 else None
    _kill_old_instances(serial)

    # 最多守护 8 小时，避免用户忘了关 FGO 时永久残留
    deadline = time.time() + 8 * 3600
    offline_since = None
    parent_gone = False
    while time.time() < deadline:
        # 【2026-11-01 关窗即全收】主脚本（父进程）死了 = 用户关了 CMD 窗口
        # → 直接收工：清代理 + 停 mitmdump + 关模拟器（不等 FGO 退出）
        if parent_pid and not _parent_alive(parent_pid):
            parent_gone = True
            _log("检测到主脚本已退出（CMD 窗口被关闭/进程被杀），执行收工："
                 "清代理 → 停 mitmdump → 关闭模拟器")
            break
        state = _probe_fgo(adb, serial, pkg)
        if state == "running":
            offline_since = None
            time.sleep(3)
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
                ac._kill_by_port(port)
                return RC_OFFLINE
            time.sleep(OFFLINE_POLL)
            continue
        break  # gone：设备在线且 FGO 已退出 → 执行清理

    # 1) 清代理三条 + 读回校验（失败重试 3 轮）
    if not _clear_proxy_verified(adb, serial):
        _log("代理清除失败：http_proxy 仍有残留，请手动运行 一键抓包-恢复.bat")
        # mitmdump 是 PC 侧进程（taskkill 按端口），与设备在否无关，照杀
        ac._kill_by_port(port)
        if parent_gone:
            _quit_emulator(adb, serial)
        return RC_FAILED

    # 2) 停 mitmdump（netstat 按端口 PID 精确清理，含其子进程）
    ac._kill_by_port(port)

    # 3)【2026-11-01 关窗即全收】父进程死亡触发的收工：把模拟器也关掉
    if parent_gone:
        _quit_emulator(adb, serial)
        return RC_OK
    _log("清理完成：代理已删除（读回确认 null）、mitmdump 已停止")
    return RC_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
