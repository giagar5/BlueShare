import pytest

from blueshare import hid
from blueshare.config import Config
from blueshare.controller import RETURN_GAP_PX, SWEEP_STEP, Controller, Mode
from blueshare.pointer import PIN_COUNTS
from blueshare.winput import LLKHF_EXTENDED, LLMHF_INJECTED, RI_MOUSE_LEFT_DOWN, RI_MOUSE_LEFT_UP, RawMouse, Rect

MON = Rect(0, 0, 1920, 1080)
VK_A, SCAN_A = 0x41, 0x1E


class FakeLink:
    def __init__(self):
        self.connected = True
        self.calls = []

    def send_keyboard(self, report): self.calls.append(("kbd", report))
    def send_system(self, report): self.calls.append(("sys", report))
    def send_typed(self, report, gap):
        self.calls.append(("kbd", report))
        self.typed_gaps = getattr(self, "typed_gaps", []) + [gap]
        self.typed_pending = True
    def typing_pending(self): return getattr(self, "typed_pending", False)
    def cancel_typed(self):
        self.typed_pending = False
        self.calls.append(("cancel",))
    def move(self, dx, dy, wheel=0, hwheel=0):
        if dx or dy or wheel or hwheel:
            self.calls.append(("move", dx, dy, wheel, hwheel))
    def move_exact(self, dx, dy, settle=0.0, buttons=None):
        self.calls.append(("exact", dx, dy) if buttons is None else ("drag", dx, dy, buttons))
    def pause(self, seconds): self.calls.append(("pause", seconds))
    def set_buttons(self, buttons):
        self.calls.append(("buttons", buttons))
    def release_all(self): self.calls.append(("release",))

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


class FakeScreen:
    def __init__(self):
        self.pos = (960, 500)
        self.monitors = [MON]

    def cursor_pos(self): return self.pos
    def set_cursor_pos(self, x, y): self.pos = (x, y)
    def primary_monitor(self): return MON
    def monitor_at(self, x, y):
        return next((m for m in self.monitors if m.left <= x < m.right and m.top <= y < m.bottom), None)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 0.01
        return self.t


@pytest.fixture
def env():
    config = Config(calibration_width=2000, calibration_height=1000, entry_push_counts=150, return_margin=0.1,
                    away_factor=3.0, side_margin=0.1, side_away_factor=3.0,
                    unlock_method="none", calibration_reaction_s=0.0)
    link, screen, notes, saves = FakeLink(), FakeScreen(), [], []
    ctl = Controller(config, link, screen, notify=notes.append, save_config=lambda: saves.append(1), clock=Clock())
    ctl.spawn = lambda target: None  # tests drive the calibration sweep by hand
    return ctl, link, screen, notes, saves


def raw(dx=0, dy=0, flags=0, data=0):
    return RawMouse(dx, dy, flags, data, False)


def push_up(ctl, screen, times=3, step=50):
    screen.pos = (480, 0)
    for _ in range(times):
        ctl.on_raw_mouse(raw(dy=-step))


def test_push_against_top_edge_switches_to_tablet(env):
    ctl, link, screen, *_ = env
    push_up(ctl, screen, times=2)
    assert ctl.mode is Mode.LOCAL
    ctl.on_raw_mouse(raw(dy=-50))
    assert ctl.mode is Mode.REMOTE
    assert link.of("sys") == [("sys", hid.system_report(hid.SYSTEM_WAKE)), ("sys", hid.system_report(0))]
    fraction_x = round(480 / 1919 * 2000)
    assert link.of("exact") == [("exact", -PIN_COUNTS, PIN_COUNTS), ("exact", fraction_x, -20)]


def test_entry_zone_switches_just_before_the_edge(env):
    ctl, link, screen, *_ = env
    ctl.config.entry_push_counts = ctl.edge.threshold = 1
    screen.pos = (480, 5)
    ctl.on_raw_mouse(raw(dy=-1))
    assert ctl.mode is Mode.LOCAL                     # no zone: 5 px from the edge is not enough
    ctl.config.entry_zone_px = 6
    ctl.on_raw_mouse(raw(dy=-1))
    assert ctl.mode is Mode.REMOTE


def test_touching_the_edge_or_dragging_does_not_switch(env):
    ctl, link, screen, *_ = env
    screen.pos = (480, 0)
    ctl.on_raw_mouse(raw(dx=200))                     # sliding along the edge
    ctl.on_raw_mouse(raw(flags=RI_MOUSE_LEFT_DOWN))   # dragging a window to the top
    for _ in range(5):
        ctl.on_raw_mouse(raw(dy=-100))
    assert ctl.mode is Mode.LOCAL
    ctl.on_raw_mouse(raw(flags=RI_MOUSE_LEFT_UP))
    push_up(ctl, screen)
    assert ctl.mode is Mode.REMOTE


def test_offline_tablet_is_reported_once(env):
    ctl, link, screen, notes, _ = env
    link.connected = False
    push_up(ctl, screen)
    push_up(ctl, screen)
    assert ctl.mode is Mode.LOCAL
    assert len(notes) == 1 and "non connesso" in notes[0]


def test_remote_moves_are_forwarded_and_pushing_down_returns(env):
    ctl, link, screen, *_ = env
    push_up(ctl, screen)
    link.calls.clear()
    ctl.on_raw_mouse(raw(dx=10, dy=-30))              # estimated 90 up (pessimistic): y = 890
    assert link.of("move") == [("move", 10, -30, 0, 0)]
    ctl.on_raw_mouse(raw(dy=110))                     # back to the bottom edge
    ctl.on_raw_mouse(raw(dy=60))                      # within the margin
    assert ctl.mode is Mode.REMOTE
    ctl.on_raw_mouse(raw(dy=60))                      # beyond the margin
    assert ctl.mode is Mode.LOCAL
    assert ("release",) in link.calls
    expected_x = round((ctl.model.fraction_along_edge()) * 1919)
    assert screen.pos == (expected_x, RETURN_GAP_PX)


def test_mouse_hook_suppresses_only_while_forwarding(env):
    ctl, link, screen, *_ = env
    assert not ctl.on_mouse_hook(0x200, 0, 0, 0)
    push_up(ctl, screen)
    assert ctl.on_mouse_hook(0x200, 0, 0, 0)
    assert not ctl.on_mouse_hook(0x200, 0, 0, LLMHF_INJECTED)


def test_keys_go_to_the_tablet_and_local_keys_are_released_on_windows(env):
    ctl, link, screen, *_ = env
    assert not ctl.on_key(0xA2, 0x1D, 0, True)        # left Ctrl pressed on the PC
    push_up(ctl, screen)
    assert ctl.on_key(VK_A, SCAN_A, 0, True)          # forwarded and suppressed
    assert link.of("kbd")[-1] == ("kbd", bytes([0, 0, 0x04, 0, 0, 0, 0, 0]))
    assert not ctl.on_key(0xA2, 0x1D, 0, False)       # Windows must see Ctrl released
    assert ctl.on_key(VK_A, SCAN_A, 0, False)
    assert link.of("kbd")[-1] == ("kbd", bytes(8))


def test_ctrl_alt_s_toggles_and_is_never_forwarded(env):
    ctl, link, screen, *_ = env
    ctl.on_key(0xA2, 0x1D, 0, True)
    ctl.on_key(0xA4, 0x38, 0, True)
    assert ctl.on_key(0x53, 0x1F, 0, True)
    assert ctl.mode is Mode.REMOTE
    assert ctl.on_key(0x53, 0x1F, 0, True)            # auto-repeat does not toggle back
    assert ctl.mode is Mode.REMOTE
    assert ctl.on_key(0x53, 0x1F, 0, False)
    assert ctl.on_key(0x53, 0x1F, 0, True)
    assert ctl.mode is Mode.LOCAL
    assert link.of("kbd") == []


def test_altgr_fake_control_is_not_forwarded(env):
    ctl, link, screen, *_ = env
    push_up(ctl, screen)
    assert ctl.on_key(0xA2, 0x21D, 0, True)
    assert link.of("kbd") == []
    assert ctl.on_key(0xA5, 0x38, LLKHF_EXTENDED, True)
    assert link.of("kbd")[-1] == ("kbd", bytes([0x40, 0, 0, 0, 0, 0, 0, 0]))


def test_first_entry_runs_an_automatic_calibration(env):
    ctl, link, screen, notes, saves = env
    ctl.config.calibration_width = ctl.config.calibration_height = 0
    push_up(ctl, screen)
    assert ctl.mode is Mode.CALIBRATING
    assert ("exact", -PIN_COUNTS, -PIN_COUNTS) in link.calls
    ctl.on_raw_mouse(raw(dx=500, dy=500))             # the user's mouse does not move the cursor
    assert link.of("move") == []
    for _ in range(300):
        assert ctl._sweep_step()
    assert link.of("move")[-1] == ("move", 0, SWEEP_STEP, 0, 0)
    ctl.on_raw_mouse(raw(flags=RI_MOUSE_LEFT_DOWN))   # bottom edge reached
    assert ("exact", -PIN_COUNTS, 0) in link.calls
    ctl.on_raw_mouse(raw(flags=RI_MOUSE_LEFT_UP))
    for _ in range(400):
        ctl._sweep_step()
    assert link.of("move")[-1] == ("move", SWEEP_STEP, 0, 0, 0)
    assert ctl.on_key(0x0D, 0x1C, 0, True)            # Enter marks the right edge
    width, height = 400 * SWEEP_STEP, 300 * SWEEP_STEP
    assert (ctl.config.calibration_width, ctl.config.calibration_height) == (width, height)
    assert saves and ctl.mode is Mode.REMOTE
    assert not ctl._sweep_step()
    assert ("buttons", hid.MOUSE_LEFT) not in link.calls
    assert ctl.on_key(0x0D, 0x1C, 0, False)           # its key-up is swallowed
    assert link.of("kbd") == []
    margin = round(0.1 * height)
    ctl.on_raw_mouse(raw(dy=margin))                  # from the corner, up to the margin
    assert ctl.mode is Mode.REMOTE
    ctl.on_raw_mouse(raw(dy=1))
    assert ctl.mode is Mode.LOCAL


def test_early_click_during_calibration_is_ignored(env):
    ctl, link, screen, *_ = env
    ctl.start_calibration()
    ctl.on_raw_mouse(raw(flags=RI_MOUSE_LEFT_DOWN))
    assert ctl.cal_phase == 1 and ctl.mode is Mode.CALIBRATING


def test_calibration_discounts_the_reaction_time(env):
    ctl, link, screen, *_ = env
    ctl.config.calibration_reaction_s = 0.25
    now = [0.0]
    ctl.clock = lambda: now[0]
    ctl.start_calibration()
    steps = 1000
    for _ in range(steps):
        ctl._sweep_step()
    now[0] = steps * SWEEP_STEP / 200                 # swept at 200 counts/s
    ctl._calibration_mark()
    assert ctl.cal_height == steps * SWEEP_STEP - 50  # 0.25 s at 200 counts/s


def test_unlock_menu_key_is_sent_only_after_idle(env):
    ctl, link, screen, *_ = env
    ctl.config.unlock_method = "menu"
    push_up(ctl, screen)                              # first entry: the tablet has been idle
    menu = ("kbd", bytes([0, 0, 0x65, 0, 0, 0, 0, 0]))
    assert menu in link.calls
    ctl.toggle()
    link.calls.clear()
    push_up(ctl, screen)                              # back again right away: no unlock
    assert menu not in link.calls
    assert link.of("sys")                             # but still the wake-up key


def test_unlock_swipe_drags_up_with_the_left_button(env):
    ctl, link, screen, *_ = env
    ctl.config.unlock_method = "swipe"
    push_up(ctl, screen)
    drags = link.of("drag")
    assert drags[0][3] == hid.MOUSE_LEFT and drags[-1] == ("drag", 0, 0, 0)
    assert sum(d[2] for d in drags) < 0


def test_escape_cancels_calibration(env):
    ctl, link, screen, notes, saves = env
    ctl.config.calibration_width = 0
    push_up(ctl, screen)
    assert ctl.on_key(0x1B, 0x01, 0, True)
    assert ctl.mode is Mode.LOCAL and not saves
    assert ctl.on_key(0x1B, 0x01, 0, False)           # its key-up reaches nobody


def test_disconnect_while_remote_returns_to_pc(env):
    ctl, link, screen, *_ = env
    push_up(ctl, screen)
    link.connected = False
    ctl.on_connection_change(False)
    assert ctl.mode is Mode.LOCAL


LEFT_MON = Rect(-1920, 0, 0, 1080)
RIGHT_MON = Rect(1920, 200, 3840, 1280)  # offset vertically: found by probing along the edge


def test_pushing_past_a_tablet_side_goes_to_the_monitor_on_that_side(env):
    ctl, link, screen, *_ = env
    screen.monitors = [MON, LEFT_MON, RIGHT_MON]
    push_up(ctl, screen)                              # enters at x = 500 of 2000, y = 980 of 1000
    assert ctl.side_monitors == (LEFT_MON, RIGHT_MON)
    ctl.on_raw_mouse(raw(dx=-500))                    # reaches the left edge of the tablet
    ctl.on_raw_mouse(raw(dx=-200))                    # within the margin (10% of 2000)
    assert ctl.mode is Mode.REMOTE
    ctl.on_raw_mouse(raw(dx=-1))
    assert ctl.mode is Mode.LOCAL and ctl.entry_monitor == LEFT_MON
    assert screen.pos == (LEFT_MON.right - 1 - RETURN_GAP_PX, round(0.98 * 1079))
    assert ("release",) in link.calls


def test_right_side_exit_lands_on_the_left_edge_of_the_right_monitor(env):
    ctl, link, screen, *_ = env
    screen.monitors = [MON, LEFT_MON, RIGHT_MON]
    push_up(ctl, screen)
    ctl.on_raw_mouse(raw(dx=1500))
    ctl.on_raw_mouse(raw(dx=201))
    assert ctl.mode is Mode.LOCAL and ctl.entry_monitor == RIGHT_MON
    assert screen.pos == (RIGHT_MON.left + RETURN_GAP_PX, RIGHT_MON.top + round(0.98 * 1079))


def test_side_edges_without_a_monitor_or_disabled_do_nothing(env):
    ctl, link, screen, *_ = env
    push_up(ctl, screen)                              # single monitor
    ctl.on_raw_mouse(raw(dx=-5000))
    assert ctl.mode is Mode.REMOTE
    ctl.toggle()
    screen.monitors = [MON, LEFT_MON]
    ctl.config.side_exits = False
    push_up(ctl, screen)
    ctl.on_raw_mouse(raw(dx=-5000))
    assert ctl.mode is Mode.REMOTE
