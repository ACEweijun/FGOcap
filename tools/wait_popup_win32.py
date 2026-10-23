# -*- coding: utf-8 -*-
"""
wait_popup_win32.py — 纯 ctypes 原生 Win32 弹窗（无外部依赖）

用途 1（独立进程）：
    pythonw wait_popup_win32.py -Message "..." -SignalFile "..."

用途 2（同进程内）：
    popup = WaitPopup("message", Path("signal.tmp"))
    ...
    popup.close()
"""

import argparse
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path


# ========================== Win32 常量 ==========================
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
IDC_ARROW = 32512
IDI_INFORMATION = 32516
SW_SHOWNORMAL = 1
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
PM_REMOVE = 0x0001
COLOR_WINDOW = 5

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_PAINT = 0x000F
GWLP_USERDATA = -21
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
DT_WORDBREAK = 0x00000010
TRANSPARENT = 1

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

# 关键 restype/argtypes
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.restype = ctypes.c_long
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.restype = ctypes.c_bool
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetTextColor.restype = ctypes.c_uint
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), ctypes.c_uint]
gdi32.CreateFontW.restype = ctypes.c_void_p
gdi32.CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                              wintypes.LPCWSTR]


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", ctypes.c_uint),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", ctypes.c_uint),
        ("pt", wintypes.POINT),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", ctypes.c_bool),
        ("rcPaint", wintypes.RECT),
        ("fRestore", ctypes.c_bool),
        ("fIncUpdate", ctypes.c_bool),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.DeleteObject.restype = ctypes.c_bool
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
user32.InvalidateRect.restype = ctypes.c_bool
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), ctypes.c_bool]


# ========================== WaitPopup ==========================
class WaitPopup:
    """非模态顶层 Win32 弹窗，信号文件出现时自动关闭（也可被用户关闭按钮关闭）。"""

    _class_registered = False
    _class_lock = threading.Lock()
    _current = None  # 最近一个实例（WndProc 闭包引用）

    def __init__(self, message, signal_file, title="FGO 一键抓包",
                 width=440, height=140, font_size=14, phase_file=None):
        # 窗口类名必须带 PID！RegisterClassExW 是系统级注册（同一桌面会话共享）。
        # 若上一轮的弹窗进程残留，用固定类名会导致注册失败
        # （ERROR_CLASS_ALREADY_EXISTS=1410）→ CreateWindowEx 返回 NULL →
        # hwnd 为空 → 进程 sys.exit(1) 秒退 → 主脚本误判成"用户关闭弹窗"。
        self.CLASS_NAME = f"FGO_WaitPopup_Class_{os.getpid()}"
        self.message = message
        self.signal_file = Path(signal_file)
        self.phase_file = Path(phase_file) if phase_file else None
        self.title = title
        self.width = width
        self.height = height
        self.font_size = font_size
        self.hwnd = None
        self._closed = False
        self._elapsed = 0  # 自绘用的倒计时
        self._ready = threading.Event()
        self._done = threading.Event()
        self._wndproc_ref = None  # 防止 WNDPROC 被 GC

        self._register_class()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        # 等待窗口创建；模拟器运行时系统负载高，窗口创建可能慢，
        # 3 秒超时会误判创建失败 → main() sys.exit(1) 秒退。放宽到 15 秒。
        self._ready.wait(timeout=15)

    def _register_class(self):
        with WaitPopup._class_lock:
            WaitPopup._current = self  # WndProc 需要访问实例属性
            if WaitPopup._class_registered:
                return
            self.hinstance = kernel32.GetModuleHandleW(None)

            def wnd_proc(hwnd, msg, wp, lp):
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                if msg == WM_PAINT:
                    # 自绘：避免依赖 STATIC 控件，直接用 DrawTextW 画文本
                    try:
                        self_obj = WaitPopup._current
                    except Exception:
                        self_obj = None
                    ps = PAINTSTRUCT()
                    hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                    if hdc and self_obj is not None:
                        # 字体
                        hfont = gdi32.CreateFontW(
                            -self_obj.font_size, 0, 0, 0, 400, 0, 0, 0, 1,
                            0, 0, 0, 0,
                            "Microsoft YaHei UI",
                        )
                        old_font = gdi32.SelectObject(hdc, hfont)
                        gdi32.SetBkMode(hdc, TRANSPARENT)
                        gdi32.SetTextColor(hdc, 0x00000000)
                        # 主消息（多行居中）
                        rect1 = wintypes.RECT(15, 12, self_obj.width - 15, self_obj.height - 50)
                        user32.DrawTextW(
                            hdc, self_obj.message, -1, ctypes.byref(rect1),
                            DT_CENTER | DT_VCENTER | DT_WORDBREAK,
                        )
                        # 计时（单行居中）
                        rect2 = wintypes.RECT(15, self_obj.height - 50,
                                              self_obj.width - 15, self_obj.height - 15)
                        elapsed_text = f"已等待 {self_obj._elapsed} 秒…"
                        user32.DrawTextW(
                            hdc, elapsed_text, -1, ctypes.byref(rect2),
                            DT_CENTER | DT_VCENTER | DT_SINGLELINE,
                        )
                        gdi32.SelectObject(hdc, old_font)
                        gdi32.DeleteObject(hfont)
                    user32.EndPaint(hwnd, ctypes.byref(ps))
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wp, lp)

            self._wndproc_ref = WNDPROC(wnd_proc)
            wc = WNDCLASSEX()
            wc.cbSize = ctypes.sizeof(wc)
            wc.style = CS_HREDRAW | CS_VREDRAW
            wc.lpfnWndProc = self._wndproc_ref
            wc.cbClsExtra = 0
            wc.cbWndExtra = 0
            wc.hInstance = self.hinstance
            wc.hIcon = user32.LoadIconW(None, IDI_INFORMATION)
            wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
            wc.hbrBackground = ctypes.c_void_p(1)  # 系统 WHITE_BRUSH
            wc.lpszMenuName = None
            wc.lpszClassName = self.CLASS_NAME
            wc.hIconSm = wc.hIcon
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                self._log_error(
                    f"RegisterClassExW 失败 GetLastError={kernel32.GetLastError()} "
                    f"class={self.CLASS_NAME}"
                )
                return
            WaitPopup._class_registered = True

    def _log_error(self, text):
        """窗口创建失败等异常写日志（pythonw 无控制台，只能落文件）。"""
        try:
            with open(Path(__file__).parent / "wait_popup_err.log",
                      "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except Exception:
            pass

    def _run(self):
        try:
            # 窗口居中坐标（先创建再调整）
            CW_USEDEFAULT = 0x80000000
            self.hwnd = user32.CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                self.CLASS_NAME,
                self.title,
                WS_OVERLAPPEDWINDOW,
                CW_USEDEFAULT, CW_USEDEFAULT, self.width, self.height,
                None, None, self.hinstance, None,
            )
            if not self.hwnd:
                self._log_error(
                    f"CreateWindowExW 失败 GetLastError={kernel32.GetLastError()} "
                    f"class={self.CLASS_NAME}"
                )
                self._ready.set()
                self._done.set()
                return

            # 居中 + 顶置
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            x = max(0, (screen_w - self.width) // 2)
            y = max(0, (screen_h - self.height) // 2)
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, x, y,
                                self.width, self.height, SWP_SHOWWINDOW)

            user32.ShowWindow(self.hwnd, SW_SHOWNORMAL)
            user32.SetForegroundWindow(self.hwnd)
            user32.SetActiveWindow(self.hwnd)
            self._ready.set()

            # 消息循环 + 计时器
            msg_struct = MSG()
            start = time.time()
            rect = wintypes.RECT(0, 0, self.width, self.height)
            last_phase_text = self.message
            last_phase_mtime = None
            exit_reason = "unknown"
            ticks = 0
            while not self._closed:
                ticks += 1
                # 关闭条件：信号文件
                if self.signal_file.exists():
                    exit_reason = f"signal_file 出现（第 {ticks} 次循环，存活 {int(time.time()-start)}s）"
                    break
                # 阶段文案更新（外部可远程切换文案，mtime 防抖）
                if self.phase_file is not None:
                    try:
                        st = self.phase_file.stat()
                        if st.st_mtime != last_phase_mtime:
                            last_phase_mtime = st.st_mtime
                            try:
                                phase_text = self.phase_file.read_text(
                                    encoding="utf-8", errors="ignore"
                                ).strip()
                            except Exception:
                                phase_text = ""
                            if phase_text and phase_text != last_phase_text:
                                self.message = phase_text
                                last_phase_text = phase_text
                                # 重置计时，让「已等待 X 秒」重头算
                                start = time.time()
                                self._elapsed = 0
                                # 全窗口重绘
                                user32.InvalidateRect(self.hwnd, None, True)
                    except Exception:
                        pass
                # 更新计时文字
                elapsed = int(time.time() - start)
                if elapsed != self._elapsed:
                    self._elapsed = elapsed
                    # 触发重绘（让 WndProc 的 WM_PAINT 重画底部文字）
                    user32.InvalidateRect(self.hwnd, ctypes.byref(rect), False)
                # 泵消息
                got = user32.PeekMessageW(ctypes.byref(msg_struct), None, 0, 0, PM_REMOVE)
                if got:
                    if msg_struct.message == WM_QUIT:
                        exit_reason = (
                            f"WM_QUIT（窗口被销毁，通常是用户点关闭；"
                            f"第 {ticks} 次循环，存活 {int(time.time()-start)}s）"
                        )
                        break
                    user32.TranslateMessage(ctypes.byref(msg_struct))
                    user32.DispatchMessageW(ctypes.byref(msg_struct))
                else:
                    time.sleep(0.3)

            user32.DestroyWindow(self.hwnd)
            # 记录退出原因（正常退出也记，便于排查「弹窗自己消失」）
            self._log_error(
                f"[EXIT] pid={os.getpid()} reason={exit_reason} "
                f"signal={self.signal_file}"
            )
        except Exception as e:
            import traceback
            try:
                with open(Path(__file__).parent / "wait_popup_err.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {e!r}\n{traceback.format_exc()}\n")
            except Exception:
                pass
        finally:
            self._closed = True
            self._done.set()

    def close(self, timeout=5):
        self._closed = True
        if self.signal_file and not self.signal_file.exists():
            try:
                self.signal_file.write_text("done", encoding="utf-8")
            except Exception:
                pass
        self._done.wait(timeout=timeout)

    def wait(self, timeout=None):
        if timeout is None:
            self._done.wait()
        else:
            self._done.wait(timeout=timeout)


def _log(msg):
    """弹窗进程的运行日志（pythonw 无控制台，只能落文件，方便主进程诊断）。"""
    try:
        with open(Path(__file__).parent / "wait_popup_err.log",
                  "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-Message", default="等待抓包数据…")
    parser.add_argument("-SignalFile", required=True)
    parser.add_argument("-Title", default="FGO 一键抓包")
    parser.add_argument("-PhaseFile", default=None,
                        help="阶段文案文件路径；主进程改这个文件内容，弹窗自动切换文案")
    args = parser.parse_args()

    _log(f"popup 启动 PID={os.getpid()}")

    signal = Path(args.SignalFile)
    if signal.exists():
        signal.unlink()
    phase = Path(args.PhaseFile) if args.PhaseFile else None
    popup = WaitPopup(args.Message, signal, title=args.Title, phase_file=phase)
    if not popup.hwnd:
        _log(f"窗口创建失败（等待 15 秒后 hwnd 仍为空），sys.exit(1)")
        sys.exit(1)
    _log(f"窗口创建成功 hwnd={popup.hwnd}")
    popup.wait(timeout=3600)
    _log("popup 正常结束（信号文件触发或消息循环退出）")


if __name__ == "__main__":
    main()
