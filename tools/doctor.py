# -*- coding: utf-8 -*-
"""
FGO 一键抓包 · 环境自检（Doctor）

用法：
    python doctor.py            人工看（双击「一键抓包-自检.bat」等价）
    python doctor.py --json     机器可读（给 agent / 脚本消费）

原则：全程只读 —— 不动代理、不动 CA、不动 FGO、不启停 mitmdump。
退出码：0 = 无阻塞项；1 = 有阻塞项（缺依赖 / 没设备 / 没装 FGO）。
"""

import json as _json
import subprocess
import sys
import time
from pathlib import Path

import auto_capture as ac

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_TAG = {OK: "[OK]  ", WARN: "[!!]  ", FAIL: "[FAIL]"}
_CREATE_NO_WINDOW = 0x08000000


def _w(s):
    """显示宽度：cmd 里中文占 2 列。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))


def _pad(s, width):
    s = str(s)
    return s + " " * max(0, width - _w(s))


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, name, detail, hint=""):
        self.rows.append((level, name, str(detail), hint))

    def render(self):
        width = max((_w(n) for _, n, _, _ in self.rows), default=0)
        out = []
        for level, name, detail, hint in self.rows:
            out.append(f"{_TAG[level]} {_pad(name, width)}  {detail}")
            if hint and level != OK:
                out.append(f"       {' ' * width}  → {hint}")
        return "\n".join(out)


def check_python(rep):
    v = sys.version_info
    ver = ".".join(map(str, v[:3]))
    if v[:2] >= (3, 11):
        rep.add(OK, "Python", ver)
    else:
        rep.add(FAIL, "Python", f"{ver}（过低）",
                "需 3.11+（fgoaddon.py 用 enum.StrEnum）：https://www.python.org/downloads/")


def check_mitmdump(rep):
    try:
        exe = ac.resolve_mitmdump()[0]
    except FileNotFoundError:
        rep.add(FAIL, "mitmdump", "未找到可执行文件",
                "pip install mitmproxy，或把免安装版解压到 tools/mitmproxy-12.2.3/")
        return
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True,
                           timeout=30, creationflags=_CREATE_NO_WINDOW)
        ver = next((ln.strip() for ln in (r.stdout or "").splitlines()
                    if "Mitmproxy" in ln), "")
    except Exception as e:
        rep.add(FAIL, "mitmdump", f"{Path(exe).name} 无法执行（{e}）",
                "重装 mitmproxy 后重开终端")
        return
    rep.add(OK, "mitmdump", f"{Path(exe).name}  {ver or '（版本号未输出）'}")


def check_adb(rep):
    adb = ac.find_adb()
    if not adb:
        rep.add(FAIL, "adb", "未找到",
                "启动模拟器；装完雷电/MuMu 要重开终端（PATH 在进程启动时读取）")
        return None
    rep.add(OK, "adb", adb)
    return adb


def check_device(rep, adb):
    if not adb:
        rep.add(FAIL, "模拟器", "跳过（没 adb）", "")
        return None
    serial = ac.find_serial(adb)
    if not serial:
        rep.add(FAIL, "模拟器", "adb 没连上任何设备",
                "启动模拟器并等它完全开机；多开实例只留一个在跑")
        return None
    rel = ac.adb_shell(adb, ["getprop", "ro.build.version.release"], serial, timeout=10)
    sdk = ac.adb_shell(adb, ["getprop", "ro.build.version.sdk"], serial, timeout=10)
    model = ac.adb_shell(adb, ["getprop", "ro.product.model"], serial, timeout=10)
    boot = ac.adb_shell(adb, ["getprop", "sys.boot_completed"], serial, timeout=10).strip()
    detail = f"{serial}   Android {rel or '?'} (API {sdk or '?'})   {model or ''}".rstrip()
    if boot != "1":
        rep.add(WARN, "模拟器", detail + "   [尚未开机完成]", "等 boot_completed=1 再跑抓包")
    else:
        rep.add(OK, "模拟器", detail)
    return serial


def check_fgo(rep, adb, serial):
    if not (adb and serial):
        rep.add(WARN, "FGO 安装", "未检查（模拟器没连上）", "启动模拟器后重跑一次自检")
        return
    pkgs = ac.detect_fgo_packages(adb, serial)
    if not pkgs:
        rep.add(FAIL, "FGO 安装", "模拟器内没找到 FGO（按 fate/fgo 匹配）",
                "先在模拟器里装好 FGO")
        return
    chosen = ac.load_package_override()
    if not chosen:
        for cand in ac.FGO_PACKAGE_CANDIDATES:
            if cand in pkgs:
                chosen = cand
                break
    chosen = chosen or pkgs[0]
    others = [p for p in pkgs if p != chosen]
    detail = f"将使用 {chosen}"
    if others:
        detail += f"（另有 {len(others)} 个：{', '.join(others)}；改用别的在 config.ini 写 package=）"
    rep.add(OK, "FGO 安装", detail)


def check_ca(rep, adb, serial):
    ca = ac.mitm_ca_path()
    if not ca:
        rep.add(WARN, "CA 证书", "主机还没有 mitmproxy CA",
                "跑过一次抓包（或手动执行一次 mitmdump）即自动生成；没它抓包必失败")
        return
    try:
        name = ac.cert_subject_hash_old(ca.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        rep.add(FAIL, "CA 证书", f"解析失败（{e}）",
                "删掉 ~/.mitmproxy 后重跑 mitmdump 重新生成")
        return
    if not (adb and serial):
        rep.add(WARN, "CA 证书", f"{name}（无设备，未验证）", "")
    elif ac.emulator_has_ca(adb, serial, name):
        rep.add(OK, "CA 证书", f"模拟器已信任（{name}）")
    else:
        rep.add(WARN, "CA 证书", f"模拟器未安装（{name}）",
                "模拟器需开 ROOT 权限；抓包时脚本会自动补装")


def check_proxy(rep, adb, serial):
    if not (adb and serial):
        return
    cur = ac.adb_shell(adb, ["settings", "get", "global", "http_proxy"], serial, timeout=10).strip()
    if not cur or cur == "null":
        rep.add(OK, "代理状态", "干净（无残留）")
    else:
        rep.add(WARN, "代理状态", f"有残留 http_proxy={cur}",
                "双击「一键抓包-恢复.bat」清干净（或等 FGO 退出后守护自动清）")


def check_port(rep):
    if ac.port_listening(ac.PORT):
        rep.add(WARN, f"端口 {ac.PORT}", "被占用",
                "可能是残留 mitmdump；抓包脚本启动时会自动清理，也可跑「一键抓包-恢复.bat」")
    else:
        rep.add(OK, f"端口 {ac.PORT}", "空闲")


def check_lan(rep):
    ip = ac.get_lan_ip()
    if not ip:
        rep.add(WARN, "本机 IP", "取不到局域网 IP",
                "FGO 会连不上代理；检查网卡/网线状态")
    else:
        rep.add(OK, "本机 IP", f"{ip}   （FGO 通过这个地址连你）")


def check_output(rep):
    d = ac.TOPLOGIN_DIR
    if not d.exists():
        rep.add(OK, "输出目录", f"{d.name}/ 尚不存在，首次抓包自动创建")
        return
    files = sorted(d.glob("toplogin_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        rep.add(OK, "输出目录", f"{d.name}/ 为空")
        return
    age_h = (time.time() - files[0].stat().st_mtime) / 3600
    rep.add(OK, "输出目录", f"{len(files)} 个文件，最新 {files[0].name}（{age_h:.1f} 小时前）")


def check_config(rep):
    cfg = ac.load_autotap_config()
    bits = [
        f"auto_tap={'开' if cfg['auto_tap'] else '关'}",
        f"tap_timeout={cfg['tap_timeout']}s",
        f"status_popup={'开' if cfg.get('status_popup') else '关'}",
        f"package={ac.load_package_override() or '自动探测'}",
    ]
    rep.add(OK, "config.ini", "   ".join(bits))


def main():
    as_json = "--json" in sys.argv[1:]
    rep = Report()
    check_python(rep)
    check_mitmdump(rep)
    adb = check_adb(rep)
    serial = check_device(rep, adb)
    check_fgo(rep, adb, serial)
    check_ca(rep, adb, serial)
    check_proxy(rep, adb, serial)
    check_port(rep)
    check_lan(rep)
    check_output(rep)
    check_config(rep)

    fails = [r for r in rep.rows if r[0] == FAIL]
    warns = [r for r in rep.rows if r[0] == WARN]

    if as_json:
        print(_json.dumps({
            "ok": not fails,
            "blockers": len(fails),
            "warnings": len(warns),
            "checks": [{"level": l, "name": n, "detail": d, "hint": h}
                       for l, n, d, h in rep.rows],
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 62)
        print("FGO 一键抓包 · 环境自检")
        print("=" * 62)
        print(rep.render())
        print("-" * 62)
        if fails:
            print(f"结论：有 {len(fails)} 项阻塞，先修完再抓"
                  + (f"（另有 {len(warns)} 项提醒）" if warns else ""))
        else:
            print("结论：可以开抓" + (f"（另有 {len(warns)} 项提醒，不挡抓）" if warns else ""))
        print("抓包用「一键抓包.bat」；清残留/卸 CA 用「一键抓包-恢复.bat」")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())