"""Keyboard layout translation: a US layout on the PC typing on an Italian tablet."""
import pytest

from blueshare import keyboard_layout as kl
from blueshare.config import Config
from blueshare.controller import Controller, Mode

from test_controller import Clock, FakeLink, FakeScreen, push_up

LSHIFT = (0xA0, 0x2A)        # vk, scan
RALT = (0xA5, 0x38)
LCTRL = (0xA2, 0x1D)
KEY_2 = (0x32, 0x03)
KEY_SEMICOLON = (0xBA, 0x27)  # US ";" key: "ò" on an Italian layout
KEY_C = (0x43, 0x2E)
KEY_A = (0x41, 0x1E)
CAPS = (0x14, 0x3A)


class FakeTranslator:
    """US PC -> Italian tablet for a few keys."""
    active = True

    def __init__(self):
        self.caps_on = False

    def caps_lock_on(self):
        return self.caps_on

    def translate(self, vk, scan, shift, altgr, caps, shortcut):
        if shortcut:
            return (0x06, None) if scan == 0x2E else None
        if scan == 0x03 and shift:
            return (0x33, 0x40)                       # "@" = AltGr + ò
        if scan == 0x27 and not shift:
            return (0x36, 0x02)                       # ";" = Shift + ,
        if scan == 0x1E:
            return (0x04, 0x02 if shift != caps else 0)
        return None


@pytest.fixture
def env():
    config = Config(calibration_width=2000, calibration_height=1000, unlock_method="none")
    link, screen = FakeLink(), FakeScreen()
    ctl = Controller(config, link, screen, notify=lambda m: None, save_config=lambda: None, clock=Clock())
    ctl.translator = FakeTranslator()
    push_up(ctl, screen)
    assert ctl.mode is Mode.REMOTE
    link.calls.clear()
    return ctl, link


def press(ctl, key, down=True, flags=0):
    return ctl.on_key(key[0], key[1], flags, down)


def reports(link):
    return [c[1] for c in link.of("kbd")]


def test_shifted_symbol_is_sent_with_the_tablet_modifiers(env):
    ctl, link = env
    press(ctl, LSHIFT)
    assert reports(link)[-1] == bytes([0x02, 0, 0, 0, 0, 0, 0, 0])
    press(ctl, KEY_2)                                  # "@" on the US layout
    assert reports(link)[-1] == bytes([0x40, 0, 0x33, 0, 0, 0, 0, 0])
    press(ctl, KEY_2)                                  # auto-repeat: nothing new
    assert len(reports(link)) == 2
    press(ctl, KEY_2, down=False)
    assert reports(link)[-1] == bytes([0x02, 0, 0, 0, 0, 0, 0, 0])   # Shift still held
    press(ctl, LSHIFT, down=False)
    assert reports(link)[-1] == bytes(8)


def test_unshifted_key_can_need_shift_on_the_tablet(env):
    ctl, link = env
    press(ctl, KEY_SEMICOLON)
    assert reports(link)[-1] == bytes([0x02, 0, 0x36, 0, 0, 0, 0, 0])
    press(ctl, KEY_SEMICOLON, down=False)
    assert reports(link)[-1] == bytes(8)


def test_shortcuts_keep_the_users_modifiers(env):
    ctl, link = env
    press(ctl, LCTRL)
    press(ctl, KEY_C)
    assert reports(link)[-1] == bytes([0x01, 0, 0x06, 0, 0, 0, 0, 0])


def test_caps_lock_reaches_windows_and_its_state_drives_translation(env):
    ctl, link = env
    assert not press(ctl, CAPS)                        # not suppressed: Windows toggles it and the LED
    assert not press(ctl, CAPS, down=False)
    assert reports(link) == []                         # the tablet never gets it
    ctl.translator.caps_on = True                      # Windows' state after the toggle
    press(ctl, KEY_A)
    assert reports(link)[-1] == bytes([0x02, 0, 0x04, 0, 0, 0, 0, 0])
    press(ctl, KEY_A, down=False)
    ctl.translator.caps_on = False
    press(ctl, KEY_A)
    assert reports(link)[-1] == bytes([0, 0, 0x04, 0, 0, 0, 0, 0])


def test_tablet_caps_lock_is_turned_off_while_translating(env):
    ctl, link = env
    ctl.on_tablet_leds(0x02)                           # the tablet reports Caps Lock on
    assert reports(link) == [bytes([0, 0, 0x39, 0, 0, 0, 0, 0]), bytes(8)]
    ctl.on_tablet_leds(0x00)                           # its answer after the toggle: nothing more
    ctl.on_tablet_leds(0x01)                           # Num Lock only: left alone
    assert len(reports(link)) == 2


def test_tablet_caps_lock_is_left_alone_by_position_or_offline(env):
    ctl, link = env
    link.connected = False
    ctl.on_tablet_leds(0x02)
    assert reports(link) == []
    link.connected = True
    ctl.on_connection_change(True)                     # retried on connection
    assert reports(link)[0] == bytes([0, 0, 0x39, 0, 0, 0, 0, 0])
    link.calls.clear()
    ctl.translator.active = False
    ctl.on_tablet_leds(0x02)
    assert reports(link) == []


def test_untranslatable_keys_fall_back_to_position(env):
    ctl, link = env
    press(ctl, (0x51, 0x10))                           # Q: not in the fake table
    assert reports(link)[-1] == bytes([0, 0, 0x14, 0, 0, 0, 0, 0])


def test_translated_key_up_after_switching_back_reaches_nobody(env):
    ctl, link = env
    press(ctl, KEY_SEMICOLON)
    ctl.toggle()
    assert ctl.mode is Mode.LOCAL
    assert press(ctl, KEY_SEMICOLON, down=False)       # swallowed, not delivered to Windows


@pytest.fixture(scope="module")
def italian():
    return kl.build_table("00000410")


def test_real_italian_table(italian):
    assert italian["a"] == (0x04, 0)
    assert italian["A"] == (0x04, kl.MOD_SHIFT)
    assert italian["@"] == (0x33, kl.MOD_ALTGR)        # AltGr + ò
    assert italian[";"] == (0x36, kl.MOD_SHIFT)        # Shift + ,
    assert italian["\u20ac"] == (0x08, kl.MOD_ALTGR)   # AltGr + e
    assert "~" not in italian                          # not on the Windows Italian layout


def test_real_translation_from_us_to_italian(monkeypatch):
    us, _ = kl._loaded_layout("00000409")
    translator = kl.LayoutTranslator("00000410")
    monkeypatch.setattr(kl.LayoutTranslator, "_pc_layout", staticmethod(lambda: us))
    assert translator.translate(0x32, 0x03, shift=True, altgr=False, caps=False, shortcut=False) == (0x33, 0x40)
    assert translator.translate(0xBA, 0x27, shift=False, altgr=False, caps=False, shortcut=False) == (0x36, 0x02)
    assert translator.translate(0x41, 0x1E, shift=False, altgr=False, caps=True, shortcut=False) == (0x04, 0x02)
    assert translator.translate(0x5A, 0x2C, shift=False, altgr=False, caps=False, shortcut=True) == (0x1D, None)
