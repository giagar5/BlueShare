"""HID-over-GATT report descriptor and report builders (keyboard, mouse, system control)."""
from __future__ import annotations

KEYBOARD_REPORT_ID = 1
MOUSE_REPORT_ID = 2
SYSTEM_REPORT_ID = 3

KEYBOARD_ROLLOVER = 6
AXIS_LIMIT = 32767
WHEEL_LIMIT = 127

MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE, MOUSE_BACK, MOUSE_FORWARD = 0x01, 0x02, 0x04, 0x08, 0x10
SYSTEM_WAKE = 0x04

_KEYBOARD = bytes([
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,  # Generic Desktop / Keyboard / Application
    0x85, KEYBOARD_REPORT_ID,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,  # modifiers (LeftControl..RightGUI)
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,  # reserved byte
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  # LED output
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,  # LED padding
    0x95, KEYBOARD_ROLLOVER, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,  # key array
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,
    0xC0,
])

_MOUSE = bytes([
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01,  # Generic Desktop / Mouse / Application
    0x85, MOUSE_REPORT_ID,
    0x09, 0x01, 0xA1, 0x00,  # Pointer / Physical
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01,  # 5 buttons
    0x95, 0x05, 0x75, 0x01, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x81, 0x01,  # padding
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,  # X, Y: 16-bit relative
    0x16, 0x01, 0x80, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x06,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,  # wheel
    0x05, 0x0C, 0x0A, 0x38, 0x02,  # Consumer / AC Pan (horizontal wheel)
    0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,
    0xC0, 0xC0,
])

_SYSTEM = bytes([
    0x05, 0x01, 0x09, 0x80, 0xA1, 0x01,  # Generic Desktop / System Control / Application
    0x85, SYSTEM_REPORT_ID,
    0x19, 0x81, 0x29, 0x83, 0x15, 0x00, 0x25, 0x01,  # Power Down, Sleep, Wake Up
    0x75, 0x01, 0x95, 0x03, 0x81, 0x02,
    0x95, 0x05, 0x81, 0x01,  # padding
    0xC0,
])

REPORT_MAP = _KEYBOARD + _MOUSE + _SYSTEM


def _clamp(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))


def mouse_report(buttons: int, dx: int = 0, dy: int = 0, wheel: int = 0, hwheel: int = 0) -> bytes:
    return (
        bytes([buttons & 0x1F])
        + _clamp(dx, AXIS_LIMIT).to_bytes(2, "little", signed=True)
        + _clamp(dy, AXIS_LIMIT).to_bytes(2, "little", signed=True)
        + _clamp(wheel, WHEEL_LIMIT).to_bytes(1, "little", signed=True)
        + _clamp(hwheel, WHEEL_LIMIT).to_bytes(1, "little", signed=True)
    )


def system_report(bits: int = 0) -> bytes:
    return bytes([bits & 0x07])


class KeyboardState:
    """Tracks pressed HID usages and builds 8-byte boot-compatible keyboard reports."""

    def __init__(self) -> None:
        self.modifiers = 0
        self.keys: list[int] = []

    def press(self, usage: int) -> bool:
        if 0xE0 <= usage <= 0xE7:
            bit = 1 << (usage - 0xE0)
            changed = not self.modifiers & bit
            self.modifiers |= bit
            return bool(changed)
        if usage in self.keys or len(self.keys) >= KEYBOARD_ROLLOVER:
            return False
        self.keys.append(usage)
        return True

    def release(self, usage: int) -> bool:
        if 0xE0 <= usage <= 0xE7:
            bit = 1 << (usage - 0xE0)
            changed = bool(self.modifiers & bit)
            self.modifiers &= ~bit
            return changed
        if usage not in self.keys:
            return False
        self.keys.remove(usage)
        return True

    def clear(self) -> None:
        self.modifiers = 0
        self.keys.clear()

    def report(self) -> bytes:
        return bytes([self.modifiers, 0] + self.keys + [0] * (KEYBOARD_ROLLOVER - len(self.keys)))
