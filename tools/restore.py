# -*- coding: utf-8 -*-
"""
FGO 一键抓包 · 环境恢复（Restore）

用法：
    python restore.py                  清代理 + 停 mitmdump（最常用）
    python restore.py --uninstall-ca   额外把 mitmproxy CA 从模拟器系统证书库删掉
    python restore.py --stop-fgo       先强停 FGO 再清（干净，但会关掉游戏）
    python restore.py --keep-mitm      只清代理，不停 mitmdump
    python restore.py --serial 127.0.0.1:5555

用途：脚本被强杀 / 断电 / 手动关窗后，代理或 mitmdump 残留的兜底恢复。
⚠️ 严禁用 `settings put global http_proxy :0` 清空代理（历史回归：空值会让 FGO
   直连 443 → 抓不到包），本脚本只用 delete。
"""

import argparse
import sys
import time

import auto_capture as ac

PROXY_CMDS = (
    "settings delete global http_proxy",
    "settings delete global global_http_proxy_host",
    "settings delete global global_http_proxy_port",
)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--uninstall-ca", action="store_true", help="卸载模拟器里的 mitmproxy CA")
    ap.add_argument("--stop-fgo", action="store_true", help="先强停 FGO 再清（会关游戏）")
    ap.add_argument("--keep-mitm", action="store_true", help="只清代理，不停 mitmdump")
    ap.add_argument("--serial", default=None, help="指定设备，默认自动找第一个")
    args = ap.parse_args()

    print("=" * 62)
    print("FGO 一键抓包 · 环境恢复")
    print("=" * 62)

    adb = ac.find_adb()
    if not adb:
        print("[FAIL] 未找到 adb，模拟器侧设置无法恢复")
        return 1
    print(f"[OK]   adb   {adb}")

    serial = args.serial or ac.find_serial(adb)
    if not serial:
        print("[FAIL] 没有在线设备（模拟器没开？）")
        return 1
    print(f"[OK]   设备  {serial}")

    if args.stop_fgo:
        pkg = ac.resolve_fgo_package(adb, serial)
        pid = ac.adb_shell(adb, ["shell", "pidof", pkg], serial).strip()
        if pid:
            ac.adb_shell(adb, ["shell", "am", "force-stop", pkg], serial)
            time.sleep(1)
            print(f"[OK]   FGO   已停止（{pkg}，pid={pid}）")
        else:
            print(f"[--]   FGO   未在运行（{pkg}）")

    before = ac.adb_shell(adb, ["settings", "get", "global", "http_proxy"],
                          serial, timeout=10).strip()
    print(f"[--]   清理前 http_proxy = {before or '(空)'}")
    for cmd in PROXY_CMDS:
        ac.adb_shell(adb, ["shell", cmd], serial, timeout=15)
    after = ac.adb_shell(adb, ["settings", "get", "global", "http_proxy"],
                         serial, timeout=10).strip()
    if not after or after == "null":
        print("[OK]   代理   三条 key 已 delete（http_proxy / _host / _port）")
    else:
        print(f"[!!]   代理   读回仍为 {after}；若有第三方代理 App 在跑，它会覆盖，手动去关掉")

    if args.keep_mitm:
        print("[--]   mitm   按要求保留 mitmdump")
    else:
        ac.kill_stale_mitmdump()
        if ac.port_listening(ac.PORT):
            print(f"[!!]   mitm   端口 {ac.PORT} 仍被占用，可能有别的程序占着")
        else:
            print(f"[OK]   mitm   mitmdump 已停止，端口 {ac.PORT} 已释放")

    if args.uninstall_ca:
        ca = ac.mitm_ca_path()
        if not ca:
            print("[--]   CA     主机没有 CA 文件，无需卸载")
        else:
            try:
                name = ac.cert_subject_hash_old(ca.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                name = None
                print(f"[FAIL] CA  解析失败：{e}")
            if name:
                script = (
                    "mount -o rw,remount / 2>/dev/null; "
                    "mount -o rw,remount /system 2>/dev/null; "
                    "mount -o rw,remount /apex/com.android.conscrypt 2>/dev/null; "
                    f"rm -f /system/etc/security/cacerts/{name} "
                    f"/apex/com.android.conscrypt/cacerts/{name}"
                )
                ac.adb_shell(adb, ["shell", f"su -c '{script}'"], serial, timeout=30)
                if ac.emulator_has_ca(adb, serial, name):
                    print(f"[!!]   CA     仍存在（{name}）：多半是 adbd 非 root，"
                          "在模拟器里以 root 再删一次")
                else:
                    print(f"[OK]   CA     已从模拟器系统证书库移除（{name}）")

    print("-" * 62)
    if not args.stop_fgo:
        print("[--]   未动 FGO：游戏中途若提示「与服务器连接中断」，退出 FGO 再重开即可")
    print("恢复完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())