"""
python 3.11
Updated/更新日期: 2024-05-11
Usage/用法 (CMD/PowerShell): mitmdump -p 8888 -s fgoaddon.py
"""

import base64
import datetime
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


def fmt_date(timestamp=None):
    dt = (
        datetime.datetime.fromtimestamp(timestamp)
        if timestamp
        else datetime.datetime.now()
    )
    return dt.strftime(f"%Y%m%d_%H%M%S")


def save_response(content: bytes | str, region: Region, key: str):
    filename = f"{key}_{region.value}_{fmt_date()}.json"
    folder = Path(key)
    fp = folder / filename
    folder.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        fp.write_text(content, "utf-8")
    else:
        fp.write_bytes(content)
    print(f"{fp} saved")


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
            content = base64.urlsafe_b64decode(unquote(content))
        save_response(content, region, "toplogin")


addons = [
    TopLoginAddOn(),
]
