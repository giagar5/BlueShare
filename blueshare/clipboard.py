"""Read the Windows clipboard (text only)."""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.CloseClipboard.restype = wintypes.BOOL
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

CF_UNICODETEXT = 13


class WindowsClipboard:
    @staticmethod
    def sequence() -> int:
        """Changes every time anything is copied on the PC."""
        return user32.GetClipboardSequenceNumber()

    @staticmethod
    def has_text() -> bool:
        """Instant check, safe inside a keyboard hook."""
        return bool(user32.IsClipboardFormatAvailable(CF_UNICODETEXT))

    @staticmethod
    def text() -> str | None:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        for _ in range(10):  # another program may hold the clipboard for a moment
            if user32.OpenClipboard(None):
                break
            time.sleep(0.02)
        else:
            log.warning("Clipboard busy")
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
