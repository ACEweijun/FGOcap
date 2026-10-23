"""
python 3.11
Updated/更新日期: 2024-05-11
Usage/用法 (CMD/PowerShell): mitmdump -p 8888 -s fgoaddon.py
"""

import base64
import json
import re
from enum import StrEnum
from pathlib import Path
from urllib.parse import unquote, urlparse

from mitmproxy import http


class Region(StrEnum):
    JP = "JP"
    CN_Android = "CN_Android"
    CN_iOS = "CN_iOS"
    CN_qudao = "CN_qudao"
    TW = "TW"
    NA = "NA"
    KR = "KR"


CN_REGIONS = [Region.CN_Android, Region.CN_iOS, Region.CN_qudao]
CN_TW_REGIONS = CN_REGIONS + [Region.TW]


def save_response(content: bytes | str, region: Region, key: str):
    """落盘前先校验 → 清空旧文件 → 原子写入。

    抓包落地常见两种坏数据：
      1) FGO 被中途掐断 → 响应体是半截 JSON / 半截 base64
      2) mitmdump 被强杀 → 正在写的文件只写了一半
    两种都会污染 toplogin/ 且 Chaldea 导入时报错，所以：
      · 非合法 JSON 一律不落盘（只打日志）
      · 先写 .tmp 再 replace，保证目录里出现的文件一定是完整的

    文件名固定为 <key>_<区服>.json（不带时间戳），且每次输出前清空目录里的
    旧 json —— 目录里永远只留最新一次抓包的结果，文件名稳定好找好导入。
    """
    filename = f"{key}_{region.value}.json"
    folder = Path(key)
    fp = folder / filename
    payload = content.encode("utf-8") if isinstance(content, str) else content
    try:
        json.loads(payload)
    except Exception as e:
        print(f"[skip] {filename} 不是合法 JSON，已丢弃（{e}）")
        return
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):
        try:
            old.unlink()
            print(f"[clean] 已删除旧文件 {old.name}")
        except OSError as e:
            print(f"[warn] 旧文件 {old.name} 删除失败：{e}")
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(fp)
    print(f"{fp} saved ({len(payload)} bytes)")


def get_region(
    flow: http.HTTPFlow, path1: str | None = None, path2: str | None = None
) -> Region | None:
    uri = urlparse(flow.request.url)
    hostname, path = uri.hostname, uri.path + "?" + uri.query
    if not hostname:
        return
    if "game.fate-go.jp" in hostname:
        if not path1 or path1 in path:
            return Region.JP
    elif "game.fate-go.us" in hostname:
        if not path1 or path1 in path:
            return Region.NA
    elif re.search(r"line\d-s\d-all\.fate-go\.com\.tw", hostname):
        if not path2 or path2 in path:
            return Region.TW
    elif not path2 or path2 in path:
        if re.search(r".*-ios-fate\.bilibiligame\.net", hostname):
            return Region.CN_iOS
        elif re.search(r".*-bili-fate\.bilibiligame\.net", hostname):
            return Region.CN_Android
        elif re.search(r".*-uo-fate\.bilibiligame\.net", hostname):
            return Region.CN_qudao


class TopLoginAddOn:
    def response(self, flow: http.HTTPFlow) -> None:
        if get_region(flow):
            print(flow.request.url)
        region = get_region(flow, "/login/top", "_key=toplogin")
        if not region:
            return
        print(f"Saving {region}: ", flow.request.url)
        assert flow.response
        content = flow.response.content
        assert content
        if region in CN_TW_REGIONS and content.startswith(b"ey"):
            try:
                content = base64.urlsafe_b64decode(unquote(content))
            except Exception as e:
                print(f"[skip] {region} base64 解码失败（响应可能被截断）：{e}")
                return
        save_response(content, region, "toplogin")


addons = [
    TopLoginAddOn(),
]
