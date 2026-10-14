"""Map Windows low-level keyboard events (set-1 scan codes) to HID keyboard usages.

Keys are translated by physical position, so the tablet applies its own keyboard layout.
"""
from __future__ import annotations

# (scan code, extended flag) -> HID usage (Keyboard/Keypad page 0x07)
_SCAN_TO_USAGE: dict[tuple[int, bool], int] = {
    (0x01, False): 0x29,  # Escape
    (0x0C, False): 0x2D, (0x0D, False): 0x2E,  # - =
    (0x0E, False): 0x2A, (0x0F, False): 0x2B,  # Backspace, Tab
    (0x1A, False): 0x2F, (0x1B, False): 0x30,  # [ ]
    (0x1C, False): 0x28, (0x1C, True): 0x58,  # Enter, keypad Enter
    (0x1D, False): 0xE0, (0x1D, True): 0xE4,  # left / right Control
    (0x27, False): 0x33, (0x28, False): 0x34, (0x29, False): 0x35,  # ; ' `
    (0x2A, False): 0xE1, (0x36, False): 0xE5, (0x36, True): 0xE5,  # left / right Shift
    (0x2B, False): 0x31,  # backslash (ISO: key next to Enter)
    (0x33, False): 0x36, (0x34, False): 0x37, (0x35, False): 0x38, (0x35, True): 0x54,  # , . / keypad-/
    (0x37, False): 0x55,  # keypad *
    (0x38, False): 0xE2, (0x38, True): 0xE6,  # left Alt / right Alt (AltGr)
    (0x39, False): 0x2C, (0x3A, False): 0x39,  # Space, Caps Lock
    (0x46, False): 0x47,  # Scroll Lock
    (0x47, False): 0x5F, (0x47, True): 0x4A,  # keypad 7 / Home
    (0x48, False): 0x60, (0x48, True): 0x52,  # keypad 8 / Up
    (0x49, False): 0x61, (0x49, True): 0x4B,  # keypad 9 / Page Up
    (0x4A, False): 0x56,  # keypad -
    (0x4B, False): 0x5C, (0x4B, True): 0x50,  # keypad 4 / Left
    (0x4C, False): 0x5D,  # keypad 5
    (0x4D, False): 0x5E, (0x4D, True): 0x4F,  # keypad 6 / Right
    (0x4E, False): 0x57,  # keypad +
    (0x4F, False): 0x59, (0x4F, True): 0x4D,  # keypad 1 / End
    (0x50, False): 0x5A, (0x50, True): 0x51,  # keypad 2 / Down
    (0x51, False): 0x5B, (0x51, True): 0x4E,  # keypad 3 / Page Down
    (0x52, False): 0x62, (0x52, True): 0x49,  # keypad 0 / Insert
    (0x53, False): 0x63, (0x53, True): 0x4C,  # keypad . / Delete
    (0x56, False): 0x64,  # ISO key between left Shift and Z (< >)
    (0x57, False): 0x44, (0x58, False): 0x45,  # F11, F12
    (0x5B, True): 0xE3, (0x5C, True): 0xE7,  # left / right Windows
    (0x5D, True): 0x65,  # Application (menu)
}

_LETTER_ROWS = {
    0x10: "qwertyuiop",
    0x1E: "asdfghjkl",
    0x2C: "zxcvbnm",
}
_LETTER_USAGE = {chr(ord("a") + i): 0x04 + i for i in range(26)}
for _start, _letters in _LETTER_ROWS.items():
    for _offset, _letter in enumerate(_letters):
        _SCAN_TO_USAGE[(_start + _offset, False)] = _LETTER_USAGE[_letter]

for _i in range(10):  # digit row 1..9, 0
    _SCAN_TO_USAGE[(0x02 + _i, False)] = 0x1E + _i

for _i in range(10):  # F1..F10
    _SCAN_TO_USAGE[(0x3B + _i, False)] = 0x3A + _i

# Keys whose scan code is ambiguous in low-level hooks are resolved by virtual-key code.
_VK_TO_USAGE = {
    0x13: 0x48,  # Pause
    0x2C: 0x46,  # Print Screen
    0x90: 0x53,  # Num Lock
}

ALTGR_FAKE_CONTROL_FLAG = 0x200


def hid_usage(vk: int, scan: int, extended: bool) -> int | None:
    if vk in _VK_TO_USAGE:
        return _VK_TO_USAGE[vk]
    return _SCAN_TO_USAGE.get((scan & 0xFF, extended))
