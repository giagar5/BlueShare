"""Windows input plumbing: raw mouse input, low-level hooks and cursor/monitor helpers (ctypes)."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HHOOK = wintypes.HANDLE
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT, WM_INPUT = 0x0012, 0x00FF
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLKHF_EXTENDED, LLKHF_INJECTED = 0x01, 0x10
LLMHF_INJECTED = 0x01
RIDEV_INPUTSINK, RIDEV_REMOVE = 0x00000100, 0x00000001
RID_INPUT, RIM_TYPEMOUSE = 0x10000003, 0
MOUSE_MOVE_ABSOLUTE = 0x01
HWND_MESSAGE = wintypes.HWND(-3)
MONITOR_DEFAULTTONULL, MONITOR_DEFAULTTOPRIMARY = 0, 1

RI_MOUSE_LEFT_DOWN, RI_MOUSE_LEFT_UP = 0x0001, 0x0002
RI_MOUSE_RIGHT_DOWN, RI_MOUSE_RIGHT_UP = 0x0004, 0x0008
RI_MOUSE_MIDDLE_DOWN, RI_MOUSE_MIDDLE_UP = 0x0010, 0x0020
RI_MOUSE_BUTTON4_DOWN, RI_MOUSE_BUTTON4_UP = 0x0040, 0x0080
RI_MOUSE_BUTTON5_DOWN, RI_MOUSE_BUTTON5_UP = 0x0100, 0x0200
RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL = 0x0400, 0x0800


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class _RawMouseButtons(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", ctypes.c_short)]


class _RawMouseUnion(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("b", _RawMouseButtons)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("u", _RawMouseUnion), ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT_MOUSE(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _sig(func, restype, *argtypes):
    func.restype = restype
    func.argtypes = list(argtypes)
    return func


_sig(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32.UnregisterClassW, wintypes.BOOL, wintypes.LPCWSTR, wintypes.HINSTANCE)
_sig(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
     wintypes.LPVOID)
_sig(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.RegisterRawInputDevices, wintypes.BOOL, ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
_sig(user32.GetRawInputData, wintypes.UINT, wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
     ctypes.POINTER(wintypes.UINT), wintypes.UINT)
_sig(user32.SetWindowsHookExW, HHOOK, ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_sig(user32.UnhookWindowsHookEx, wintypes.BOOL, HHOOK)
_sig(user32.CallNextHookEx, LRESULT, HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_sig(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
_sig(user32.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_sig(user32.SetCursorPos, wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_sig(user32.MonitorFromPoint, wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
_sig(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_sig(kernel32.GetCurrentThreadId, wintypes.DWORD)
_sig(kernel32.CreateMutexW, wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)

ERROR_ALREADY_EXISTS = 183


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int   # exclusive
    bottom: int  # exclusive

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class WindowsScreen:
    """Cursor and monitor queries in physical pixels (the process must be per-monitor DPI aware)."""

    def cursor_pos(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def set_cursor_pos(self, x: int, y: int) -> None:
        user32.SetCursorPos(x, y)

    def monitor_at(self, x: int, y: int) -> Rect | None:
        handle = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONULL)
        return self._rect(handle) if handle else None

    def primary_monitor(self) -> Rect:
        rect = self._rect(user32.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY))
        assert rect is not None
        return rect

    @staticmethod
    def _rect(handle) -> Rect | None:
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        r = info.rcMonitor
        return Rect(r.left, r.top, r.right, r.bottom)


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(wintypes.HANDLE(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def acquire_single_instance(name: str = "Local\\BlueShare.SingleInstance"):
    """Returns a mutex handle to keep alive, or None if another instance is running."""
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle or ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        return None
    return handle


@dataclass
class RawMouse:
    dx: int
    dy: int
    button_flags: int
    button_data: int
    absolute: bool


RawMouseHandler = Callable[[RawMouse], None]
KeyHandler = Callable[[int, int, int, bool], bool]           # vk, scan, flags, is_down -> suppress
MouseHookHandler = Callable[[int, int, int, int], bool]      # msg, x, y, flags -> suppress


class InputThread(threading.Thread):
    """Owns a message-only window that receives raw mouse input and hosts both low-level hooks."""

    CLASS_NAME = "BlueShareInputSink"

    def __init__(self, on_raw_mouse: RawMouseHandler, on_key: KeyHandler, on_mouse_hook: MouseHookHandler) -> None:
        super().__init__(name="blueshare-input", daemon=True)
        self.on_raw_mouse = on_raw_mouse
        self.on_key = on_key
        self.on_mouse_hook = on_mouse_hook
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Exception | None = None
        # ctypes callbacks must stay referenced for the lifetime of the hooks/window
        self._wndproc = WNDPROC(self._window_proc)
        self._kbd_proc = HOOKPROC(self._keyboard_proc)
        self._mouse_proc = HOOKPROC(self._mouse_proc_impl)
        self._raw_buffer = ctypes.create_string_buffer(256)

    def start_and_wait(self, timeout: float = 5.0) -> None:
        self.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("input thread did not start")
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self.join(timeout=3)

    def run(self) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        hwnd = kbd_hook = mouse_hook = None
        try:
            self._thread_id = kernel32.GetCurrentThreadId()
            wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._wndproc,
                             hInstance=hinstance, lpszClassName=self.CLASS_NAME)
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                raise ctypes.WinError(ctypes.get_last_error())
            hwnd = user32.CreateWindowExW(0, self.CLASS_NAME, "BlueShare", 0, 0, 0, 0, 0,
                                          HWND_MESSAGE, None, hinstance, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            device = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
            if not user32.RegisterRawInputDevices(ctypes.byref(device), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                raise ctypes.WinError(ctypes.get_last_error())
            kbd_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, hinstance, 0)
            mouse_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hinstance, 0)
            if not kbd_hook or not mouse_hook:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception as e:  # reported to start_and_wait
            self._error = e
            self._ready.set()
            self._cleanup(hwnd, kbd_hook, mouse_hook, hinstance)
            return
        self._ready.set()
        log.info("Input hooks installed")

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._cleanup(hwnd, kbd_hook, mouse_hook, hinstance)
        log.info("Input hooks removed")

    def _cleanup(self, hwnd, kbd_hook, mouse_hook, hinstance) -> None:
        if kbd_hook:
            user32.UnhookWindowsHookEx(kbd_hook)
        if mouse_hook:
            user32.UnhookWindowsHookEx(mouse_hook)
        if hwnd:
            remove = RAWINPUTDEVICE(0x01, 0x02, RIDEV_REMOVE, None)
            user32.RegisterRawInputDevices(ctypes.byref(remove), 1, ctypes.sizeof(RAWINPUTDEVICE))
            user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(self.CLASS_NAME, hinstance)

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = wintypes.UINT(ctypes.sizeof(self._raw_buffer))
            got = user32.GetRawInputData(lparam, RID_INPUT, self._raw_buffer, ctypes.byref(size),
                                         ctypes.sizeof(RAWINPUTHEADER))
            if got != 0xFFFFFFFF and got >= ctypes.sizeof(RAWINPUT_MOUSE):
                raw = RAWINPUT_MOUSE.from_buffer_copy(self._raw_buffer)
                if raw.header.dwType == RIM_TYPEMOUSE:
                    m = raw.mouse
                    try:
                        self.on_raw_mouse(RawMouse(m.lLastX, m.lLastY, m.u.b.usButtonFlags, m.u.b.usButtonData,
                                                   bool(m.usFlags & MOUSE_MOVE_ABSOLUTE)))
                    except Exception:
                        log.exception("raw mouse handler failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _keyboard_proc(self, code, wparam, lparam):
        if code >= 0:
            k = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            is_down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            try:
                if self.on_key(k.vkCode, k.scanCode, k.flags, is_down):
                    return 1
            except Exception:
                log.exception("keyboard handler failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _mouse_proc_impl(self, code, wparam, lparam):
        if code >= 0:
            m = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            try:
                if self.on_mouse_hook(wparam, m.pt.x, m.pt.y, m.flags):
                    return 1
            except Exception:
                log.exception("mouse hook handler failed")
        return user32.CallNextHookEx(None, code, wparam, lparam)
