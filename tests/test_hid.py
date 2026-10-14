from blueshare import hid, keymap


def _parse(descriptor: bytes):
    """Yields (prefix, data) items; checks collections are balanced."""
    i, depth, items = 0, 0, []
    while i < len(descriptor):
        prefix = descriptor[i]
        size = (0, 1, 2, 4)[prefix & 0x03]
        data = descriptor[i + 1:i + 1 + size]
        if prefix & 0xFC == 0xA0:
            depth += 1
        elif prefix & 0xFC == 0xC0:
            depth -= 1
            assert depth >= 0
        items.append((prefix, data))
        i += 1 + size
    assert i == len(descriptor)
    assert depth == 0
    return items


def test_report_map_is_well_formed_and_declares_three_reports():
    items = _parse(hid.REPORT_MAP)
    report_ids = [data[0] for prefix, data in items if prefix == 0x85]
    assert report_ids == [hid.KEYBOARD_REPORT_ID, hid.MOUSE_REPORT_ID, hid.SYSTEM_REPORT_ID]


def test_mouse_report_layout_and_clamping():
    report = hid.mouse_report(hid.MOUSE_LEFT, -40000, 300, wheel=-1, hwheel=200)
    assert len(report) == 7
    assert report[0] == hid.MOUSE_LEFT
    assert int.from_bytes(report[1:3], "little", signed=True) == -hid.AXIS_LIMIT
    assert int.from_bytes(report[3:5], "little", signed=True) == 300
    assert int.from_bytes(report[5:6], "little", signed=True) == -1
    assert int.from_bytes(report[6:7], "little", signed=True) == hid.WHEEL_LIMIT


def test_keyboard_state_tracks_modifiers_and_keys():
    kb = hid.KeyboardState()
    assert kb.press(0xE1)          # left shift
    assert kb.press(0x04)          # a
    assert not kb.press(0x04)      # auto-repeat is not a change
    assert kb.report() == bytes([0x02, 0, 0x04, 0, 0, 0, 0, 0])
    assert kb.release(0x04) and kb.release(0xE1)
    assert kb.report() == bytes(8)


def test_keyboard_rollover_ignores_seventh_key():
    kb = hid.KeyboardState()
    for usage in range(0x04, 0x0B):
        kb.press(usage)
    assert kb.keys == list(range(0x04, 0x0A))


def test_keymap_by_physical_position():
    assert keymap.hid_usage(0x41, 0x1E, False) == 0x04        # A
    assert keymap.hid_usage(0x4D, 0x32, False) == 0x10        # M (it's a comma on AZERTY layouts, still 0x10)
    assert keymap.hid_usage(0x26, 0x48, True) == 0x52         # arrow up
    assert keymap.hid_usage(0x68, 0x48, False) == 0x60        # keypad 8
    assert keymap.hid_usage(0xA5, 0x38, True) == 0xE6         # AltGr
    assert keymap.hid_usage(0x90, 0x45, True) == 0x53         # Num Lock (by virtual key)
    assert keymap.hid_usage(0x0B, 0x0B, False) == 0x27        # 0
