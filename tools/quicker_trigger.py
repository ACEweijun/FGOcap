# -*- coding: utf-8 -*-
"""
FGO 一键抓包 — Quicker 触发版
=============================
用 pythonw.exe 运行本脚本（无控制台黑窗口，只有弹窗）。

Quicker 动作配置（把下面路径换成你自己的）：
  程序: C:/你的Python安装目录/pythonw.exe
  参数: "本脚本的绝对路径/quicker_trigger.py"

运行日志: tools/quicker_trigger.log（排错用）
"""

import sys
import traceback
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

# pythonw 下没有控制台，stdout/stderr 为 None → 重定向到日志文件
LOG_FILE = TOOLS_DIR / "quicker_trigger.log"
try:
    if sys.stdout is None:
        sys.stdout = open(LOG_FILE, "a", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
except Exception:
    pass

import auto_capture  # noqa: E402

if __name__ == "__main__":
    try:
        auto_capture.main()
    except Exception:
        try:
            sys.stderr.write(traceback.format_exc())
        except Exception:
            pass
        raise
