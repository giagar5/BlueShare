"""Type with the PC keyboard layout on a tablet that uses another one.

BlueShare sends keys by physical position and the tablet applies its own layout. When the two
layouts differ, a key is translated: Windows tells which character the PC layout produces for it
(ToUnicodeEx with the layout of the foreground window, so switching layouts on the PC just works),
and a reverse table built from the tablet's layout gives the key and modifiers that type that
character there.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from . import keymap

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
HKL = ctypes.c_void_p

user32.ToUnicodeEx.restype = ctypes.c_int
user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR,
                               ctypes.c_int, wintypes.UINT, HKL]
user32.MapVirtualKeyExW.restype = wintypes.UINT
user32.MapVirtualKeyExW.argtypes = [wintypes.UINT, wintypes.UINT, HKL]
user32.GetKeyboardLayoutList.restype = ctypes.c_int
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(HKL)]
user32.LoadKeyboardLayoutW.restype = HKL
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.UnloadKeyboardLayout.restype = wintypes.BOOL
user32.UnloadKeyboardLayout.argtypes = [HKL]
user32.GetKeyboardLayout.restype = HKL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetKeyState.restype = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]

MOD_SHIFT, MOD_ALTGR = 0x02, 0x40          # HID modifier bits: left Shift, right Alt (AltGr)
NO_STATE_CHANGE = 0x04                     # ToUnicodeEx: leave dead-key state alone (Win10 1607+)
MAPVK_VSC_TO_VK_EX = 3
KLF_NOTELLSHELL = 0x80
VK_CAPITAL = 0x14

# Keys that type a character; the others (Enter, arrows, F-keys, keypad...) keep their position.
CHAR_USAGES = frozenset(range(0x04, 0x28)) | frozenset(range(0x2D, 0x39)) | {0x64}
CAPS_LOCK_USAGE = 0x39
_USAGE_TO_SCAN = {usage: scan for (scan, ext), usage in keymap._SCAN_TO_USAGE.items()
                  if not ext and usage in CHAR_USAGES}

# Tablet layouts offered in the tray menu: name -> Windows keyboard layout id (KLID).
LAYOUTS = {
    "Italiano": "00000410",
    "Inglese (US)": "00000409",
    "Inglese (UK)": "00000809",
    "Tedesco": "00000407",
    "Francese": "0000040C",
    "Spagnolo": "0000040A",
}


def _key_state(shift: bool, altgr: bool, caps: bool):
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[0x10] = state[0xA0] = 0x80           # VK_SHIFT, VK_LSHIFT
    if altgr:
        for vk in (0x11, 0xA2, 0x12, 0xA5):        # Ctrl + Alt, as Windows sees AltGr
            state[vk] = 0x80
    if caps:
        state[VK_CAPITAL] = 0x01
    return state


def char_for(hkl, vk: int, scan: int, shift: bool = False, altgr: bool = False, caps: bool = False) -> str | None:
    """The single printable character a key types with ``hkl``; None for dead keys and non-characters."""
    buf = ctypes.create_unicode_buffer(8)
    n = user32.ToUnicodeEx(vk, scan, _key_state(shift, altgr, caps), buf, len(buf), NO_STATE_CHANGE, hkl)
    if n != 1 or buf[0] < " ":
        return None
    return buf[0]


def _loaded_layout(klid: str):
    """Returns (hkl, loaded_by_us) for a keyboard layout id, loading it if the user has not installed it."""
    count = user32.GetKeyboardLayoutList(0, None)
    handles = (HKL * count)()
    user32.GetKeyboardLayoutList(count, handles)
    lang = int(klid[-4:], 16)
    wanted = lang | lang << 16
    for hkl in handles:
        if hkl and hkl & 0xFFFFFFFF == wanted:
            return hkl, False
    hkl = user32.LoadKeyboardLayoutW(klid, KLF_NOTELLSHELL)
    if not hkl:
        raise OSError(f"keyboard layout {klid} is not available")
    return hkl, True


def reverse_table(hkl) -> dict[str, tuple[int, int]]:
    """character -> (HID usage, HID modifiers) that type it with ``hkl``, preferring fewer modifiers."""
    table: dict[str, tuple[int, int]] = {}
    for mods in (0, MOD_SHIFT, MOD_ALTGR, MOD_SHIFT | MOD_ALTGR):
        for usage, scan in sorted(_USAGE_TO_SCAN.items()):
            vk = user32.MapVirtualKeyExW(scan, MAPVK_VSC_TO_VK_EX, hkl)
            if not vk:
                continue
            ch = char_for(hkl, vk, scan, bool(mods & MOD_SHIFT), bool(mods & MOD_ALTGR))
            if ch is not None and ch not in table:
                table[ch] = (usage, mods)
    return table


def build_table(klid: str) -> dict[str, tuple[int, int]]:
    hkl, loaded = _loaded_layout(klid)
    try:
        return reverse_table(hkl)
    finally:
        if loaded:
            user32.UnloadKeyboardLayout(hkl)


class LayoutTranslator:
    """Translates keys typed on the PC into keys for the tablet layout ``klid`` ("" = by position)."""

    def __init__(self, klid: str = "") -> None:
        self.klid = ""
        self.table: dict[str, tuple[int, int]] | None = None
        self.set_target(klid)

    @property
    def active(self) -> bool:
        return self.table is not None

    def set_target(self, klid: str) -> None:
        self.klid, self.table = klid, None
        if not klid:
            return
        try:
            self.table = build_table(klid)
            log.info("Translating keys for tablet layout %s (%d characters)", klid, len(self.table))
        except OSError as e:
            log.error("Cannot load tablet layout %s (%s): keys are sent by position", klid, e)

    @staticmethod
    def caps_lock_on() -> bool:
        return bool(user32.GetKeyState(VK_CAPITAL) & 1)

    @staticmethod
    def _pc_layout():
        thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        return user32.GetKeyboardLayout(thread)

    def translate(self, vk: int, scan: int, shift: bool, altgr: bool, caps: bool,
                  shortcut: bool) -> tuple[int, int | None] | None:
        """(usage, modifiers) to send for a key, or None to send it by position.

        For shortcuts (Ctrl, Alt or Win held) only the key changes, matched on its unshifted
        character, and the user's modifiers are kept: modifiers is then None.
        """
        if self.table is None:
            return None
        hkl = self._pc_layout()
        if shortcut:
            ch = char_for(hkl, vk, scan)
            hit = self.table.get(ch) if ch else None
            return (hit[0], None) if hit and hit[1] == 0 else None
        ch = char_for(hkl, vk, scan, shift, altgr, caps)
        return self.table.get(ch) if ch else None
