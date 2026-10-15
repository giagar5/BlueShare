"""Routes keyboard and mouse to the PC or to the tablet and decides when to switch."""
from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Callable, Protocol

from . import hid, keymap
from .config import Config
from .pointer import PIN_COUNTS, SETTLE_S, EdgePush, Exit, PointerModel, Side
from .winput import (
    LLKHF_EXTENDED, LLKHF_INJECTED, LLMHF_INJECTED,
    RI_MOUSE_BUTTON4_DOWN, RI_MOUSE_BUTTON4_UP, RI_MOUSE_BUTTON5_DOWN, RI_MOUSE_BUTTON5_UP,
    RI_MOUSE_HWHEEL, RI_MOUSE_LEFT_DOWN, RI_MOUSE_LEFT_UP, RI_MOUSE_MIDDLE_DOWN, RI_MOUSE_MIDDLE_UP,
    RI_MOUSE_RIGHT_DOWN, RI_MOUSE_RIGHT_UP, RI_MOUSE_WHEEL, RawMouse, Rect,
)

log = logging.getLogger(__name__)

VK_ESCAPE, VK_RETURN, VK_SPACE, VK_S, VK_LCONTROL, VK_LMENU = 0x1B, 0x0D, 0x20, 0x53, 0xA2, 0xA4
RETURN_GAP_PX = 8           # distance from the edge where the PC cursor lands when coming back
MIN_CALIBRATION_COUNTS = 50
MAX_CALIBRATION_COUNTS = 30000
SWEEP_STEP, SWEEP_INTERVAL_S = 2, 0.015  # ~130 counts/s: slow enough to click in time, and never accelerated
WHEEL_DELTA = 120
MENU_USAGE = 0x65           # Keyboard Application key: Android KEYCODE_MENU, dismisses a swipe lock screen

_BUTTONS = [
    (RI_MOUSE_LEFT_DOWN, RI_MOUSE_LEFT_UP, hid.MOUSE_LEFT),
    (RI_MOUSE_RIGHT_DOWN, RI_MOUSE_RIGHT_UP, hid.MOUSE_RIGHT),
    (RI_MOUSE_MIDDLE_DOWN, RI_MOUSE_MIDDLE_UP, hid.MOUSE_MIDDLE),
    (RI_MOUSE_BUTTON4_DOWN, RI_MOUSE_BUTTON4_UP, hid.MOUSE_BACK),
    (RI_MOUSE_BUTTON5_DOWN, RI_MOUSE_BUTTON5_UP, hid.MOUSE_FORWARD),
]


class Mode(enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CALIBRATING = "calibrating"


class Screen(Protocol):
    def cursor_pos(self) -> tuple[int, int]: ...
    def set_cursor_pos(self, x: int, y: int) -> None: ...
    def monitor_at(self, x: int, y: int) -> Rect | None: ...
    def primary_monitor(self) -> Rect: ...


class HidLink(Protocol):
    @property
    def connected(self) -> bool: ...
    def send_keyboard(self, report: bytes) -> None: ...
    def send_system(self, report: bytes) -> None: ...
    def move(self, dx: int, dy: int, wheel: int = 0, hwheel: int = 0) -> None: ...
    def move_exact(self, dx: int, dy: int, settle: float = 0.0, buttons: int | None = None) -> None: ...
    def pause(self, seconds: float) -> None: ...
    def set_buttons(self, buttons: int) -> None: ...
    def release_all(self) -> None: ...


KeyId = tuple[int, bool]  # (scan code, extended)


class Controller:
    def __init__(self, config: Config, link: HidLink, screen: Screen,
                 notify: Callable[[str], None], save_config: Callable[[], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.link = link
        self.screen = screen
        self.notify = notify
        self.save_config = save_config
        self.clock = clock
        self.on_state_change: Callable[[], None] | None = None

        self.lock = threading.RLock()
        self.mode = Mode.LOCAL
        self.edge = EdgePush(config.entry_push_counts)
        self.model: PointerModel | None = None
        self.entry_monitor: Rect | None = None
        self.side_monitors: tuple[Rect | None, Rect | None] = (None, None)
        self.keyboard = hid.KeyboardState()

        self.phys_buttons = 0        # physical mouse buttons currently held
        self.ignored_buttons = 0     # held across a switch; not forwarded until released
        self.local_keys: set[KeyId] = set()      # held on the PC; their key-up must reach Windows
        self.forwarded_keys: set[KeyId] = set()  # held on the tablet
        self.swallow_keys: set[KeyId] = set()    # key-ups that must reach nobody
        self.lctrl = self.lalt = False
        self._frac = [0.0, 0.0]
        self._wheel = [0, 0]
        self._last_offline_notice = -1e9
        self._last_remote_input = -1e9
        # automatic calibration: the cursor sweeps slowly towards an edge until the user clicks
        self.cal_phase = 0            # 1 = sweeping down, 2 = sweeping right
        self.cal_counts = 0
        self.cal_height = 0
        self._cal_t0 = 0.0
        self._sweep_gen = 0
        self.spawn: Callable[[Callable[[], None]], None] = (
            lambda target: threading.Thread(target=target, name="blueshare-calibration", daemon=True).start())

    # ---- events from the input thread --------------------------------------------------------

    def on_mouse_hook(self, msg: int, x: int, y: int, flags: int) -> bool:
        return self.mode is not Mode.LOCAL and not flags & LLMHF_INJECTED

    def on_raw_mouse(self, m: RawMouse) -> None:
        with self.lock:
            pressed = self._track_buttons(m.button_flags)
            dx, dy = (0, 0) if m.absolute else (m.dx, m.dy)
            if self.mode is Mode.LOCAL:
                if dx or dy:
                    self._local_motion(dx, dy)
                return
            if self.mode is Mode.CALIBRATING:  # the cursor is driven by the sweep; a click marks the edge
                if pressed & hid.MOUSE_LEFT:
                    self.ignored_buttons |= hid.MOUSE_LEFT
                    self._calibration_mark()
                return

            cx, cy = self._scale(dx, dy)
            wheel, hwheel = self._wheel_steps(m.button_flags, m.button_data)
            self.link.set_buttons(self.phys_buttons & ~self.ignored_buttons)
            self.link.move(cx, cy, wheel, hwheel)
            self._last_remote_input = self.clock()
            exit = self.model.move(cx, cy, self.clock()) if self.model is not None else Exit.NONE
            if exit is Exit.RETURN:
                log.info("Pushed past the tablet edge: back to the PC")
                self._go_local()
            elif exit:
                log.info("Pushed past the tablet side edge: to the monitor on that side")
                self._go_local(exit)

    def on_key(self, vk: int, scan: int, flags: int, down: bool) -> bool:
        if flags & LLKHF_INJECTED:
            return False
        with self.lock:
            fake_ctrl = bool(scan & keymap.ALTGR_FAKE_CONTROL_FLAG)
            key: KeyId = (scan & 0xFF, bool(flags & LLKHF_EXTENDED))
            if vk == VK_LCONTROL and not fake_ctrl:
                self.lctrl = down
            elif vk == VK_LMENU:
                self.lalt = down

            if vk == VK_S and self.lctrl and self.lalt:  # Ctrl+Alt+S toggles
                if not down:
                    self.swallow_keys.discard(key)
                elif key not in self.swallow_keys:  # ignore auto-repeat
                    self.swallow_keys.add(key)
                    self.toggle()
                return True
            if not down and key in self.swallow_keys:
                self.swallow_keys.discard(key)
                return True

            if self.mode is Mode.LOCAL:
                (self.local_keys.add if down else self.local_keys.discard)(key)
                return False
            if key in self.local_keys:
                if not down:
                    self.local_keys.discard(key)
                    return False
                return True
            if fake_ctrl:
                return True
            if self.mode is Mode.CALIBRATING:
                if down and key not in self.swallow_keys:
                    if vk == VK_ESCAPE:
                        self.swallow_keys.add(key)
                        self.notify("Calibrazione annullata.")
                        self._go_local()
                    elif vk in (VK_RETURN, VK_SPACE):
                        self.swallow_keys.add(key)
                        self._calibration_mark()
                return True

            usage = keymap.hid_usage(vk, scan, key[1])
            if usage is not None:
                changed = self.keyboard.press(usage) if down else self.keyboard.release(usage)
                (self.forwarded_keys.add if down else self.forwarded_keys.discard)(key)
                if changed:
                    self.link.send_keyboard(self.keyboard.report())
                    self._last_remote_input = self.clock()
            return True

    def on_connection_change(self, connected: bool) -> None:
        with self.lock:
            if not connected and self.mode is not Mode.LOCAL:
                self._go_local()
            self.notify("Tablet connesso." if connected else "Tablet disconnesso.")
        self._changed()

    # ---- commands (hotkey, tray) -------------------------------------------------------------

    def toggle(self) -> None:
        with self.lock:
            if self.mode is not Mode.LOCAL:
                self._go_local()
                return
            x, y = self.screen.cursor_pos()
            mon = self.screen.monitor_at(x, y) or self.screen.primary_monitor()
            self._enter(self._fraction_on(mon, x, y), mon)

    def start_calibration(self, mon: Rect | None = None) -> None:
        with self.lock:
            if not self._require_connection():
                return
            if mon is None:
                x, y = self.screen.cursor_pos()
                mon = self.screen.monitor_at(x, y) or self.screen.primary_monitor()
            self._switch(Mode.CALIBRATING, mon)
            self._wake_and_unlock()
            self.link.move_exact(-PIN_COUNTS, -PIN_COUNTS, SETTLE_S)  # top-left corner
            self.cal_phase, self.cal_counts, self.cal_height = 1, 0, 0
            self._sweep_gen += 1
            self.notify("Calibrazione 1/2: il cursore del tablet scende da solo. "
                        "Fai clic (o premi Invio) appena tocca il bordo inferiore. Esc per annullare.")
            self.spawn(lambda gen=self._sweep_gen: self._sweep(gen))
        self._changed()

    def _sweep(self, gen: int) -> None:
        time.sleep(SETTLE_S)
        while self._sweep_step(gen):
            time.sleep(SWEEP_INTERVAL_S)

    def _sweep_step(self, gen: int | None = None) -> bool:
        """Moves the cursor one step towards the edge being measured; False when the sweep ends."""
        with self.lock:
            if self.mode is not Mode.CALIBRATING or self.cal_phase == 0 or gen not in (None, self._sweep_gen):
                return False
            if self.cal_counts >= MAX_CALIBRATION_COUNTS:
                self.notify("Calibrazione interrotta: il bordo non è stato segnalato.")
                self._go_local()
                return False
            if self.cal_phase == 1:
                self.link.move(0, SWEEP_STEP)
            else:
                self.link.move(SWEEP_STEP, 0)
            if self.cal_counts == 0:
                self._cal_t0 = self.clock()
            self.cal_counts += SWEEP_STEP
            return True

    def _measured_counts(self) -> int:
        """Counts swept until the click, minus the user's reaction time at the measured sweep speed."""
        elapsed = self.clock() - self._cal_t0
        rate = self.cal_counts / elapsed if elapsed > 0 else 0.0
        late = round(self.config.calibration_reaction_s * rate)
        return max(MIN_CALIBRATION_COUNTS, self.cal_counts - late)

    def _calibration_mark(self) -> None:
        if self.cal_phase == 1:
            if self.cal_counts < MIN_CALIBRATION_COUNTS:
                return  # clicked before the sweep started moving: ignore
            self.cal_height = self._measured_counts()
            self.cal_phase, self.cal_counts = 2, 0
            self.link.move_exact(-PIN_COUNTS, 0, SETTLE_S)  # bottom-left corner
            self.notify("Calibrazione 2/2: ora il cursore va verso destra. "
                        "Fai clic (o premi Invio) appena tocca il bordo destro.")
        elif self.cal_phase == 2 and self.cal_counts >= MIN_CALIBRATION_COUNTS:
            self._finish_calibration(self._measured_counts(), self.cal_height)

    def _finish_calibration(self, width: int, height: int) -> None:
        self.cal_phase = 0
        self.config.calibration_width, self.config.calibration_height = width, height
        self.save_config()
        mon = self.entry_monitor or self.screen.primary_monitor()
        self.model = self._new_model(mon)
        self.model.place(width, height)  # the tablet cursor sits in the bottom-right corner
        self._switch(Mode.REMOTE, mon)
        self.notify(f"Calibrazione salvata ({width} x {height}). Per tornare al PC spingi oltre il bordo.")
        log.info("Calibration saved: %d x %d counts", width, height)
        self._changed()

    # ---- internals ---------------------------------------------------------------------------

    def _local_motion(self, dx: int, dy: int) -> None:
        if self.phys_buttons:  # never switch while dragging (e.g. snapping a window to the top)
            self.edge.reset()
            return
        x, y = self.screen.cursor_pos()
        mon = self.screen.monitor_at(x, y)
        if mon is None:
            return
        side = self.config.side
        zone = max(0, self.config.entry_zone_px)  # > 0: switch slightly before reaching the edge
        if side is Side.TOP:
            at_edge, toward = y <= mon.top + zone and self.screen.monitor_at(x, mon.top - 1) is None, -dy
        elif side is Side.BOTTOM:
            at_edge, toward = y >= mon.bottom - 1 - zone and self.screen.monitor_at(x, mon.bottom) is None, dy
        elif side is Side.LEFT:
            at_edge, toward = x <= mon.left + zone and self.screen.monitor_at(mon.left - 1, y) is None, -dx
        else:
            at_edge, toward = x >= mon.right - 1 - zone and self.screen.monitor_at(mon.right, y) is None, dx
        if self.edge.feed(at_edge, toward, self.clock()):
            self.edge.reset()
            self._enter(self._fraction_on(mon, x, y), mon)

    def _fraction_on(self, mon: Rect, x: int, y: int) -> float:
        if self.config.side in (Side.TOP, Side.BOTTOM):
            return (x - mon.left) / max(1, mon.width - 1)
        return (y - mon.top) / max(1, mon.height - 1)

    def _enter(self, fraction: float, mon: Rect) -> None:
        if not self._require_connection():
            return
        cal = self.config.calibration
        if not cal.is_valid():
            self.start_calibration(mon)
            return
        self.model = self._new_model(mon)
        self._switch(Mode.REMOTE, mon)
        self._wake_and_unlock()
        for dx, dy, settle in self.model.enter(fraction):
            self.link.move_exact(dx, dy, settle)
        log.info("Switched to the tablet at %.2f along the edge", fraction)
        self._changed()

    def _go_local(self, exit: Exit = Exit.RETURN) -> None:
        """Back to Windows: below the tablet (return edge) or onto the monitor on the exit side."""
        mon = self.entry_monitor or self.screen.primary_monitor()
        model = self.model if self.mode is Mode.REMOTE else None
        target = None
        if exit in (Exit.BEFORE, Exit.AFTER) and model is not None:
            target = self.side_monitors[0 if exit is Exit.BEFORE else 1]
        self.link.release_all()
        self.swallow_keys |= self.forwarded_keys
        side = self.config.side
        if target is None:
            fraction = model.fraction_along_edge() if model else 0.5
            self._switch(Mode.LOCAL, mon)
            if side in (Side.TOP, Side.BOTTOM):
                x = mon.left + round(fraction * (mon.width - 1))
                y = mon.top + RETURN_GAP_PX if side is Side.TOP else mon.bottom - 1 - RETURN_GAP_PX
            else:
                y = mon.top + round(fraction * (mon.height - 1))
                x = mon.left + RETURN_GAP_PX if side is Side.LEFT else mon.right - 1 - RETURN_GAP_PX
            log.info("Switched back to the PC")
        else:
            self._switch(Mode.LOCAL, target)
            if side in (Side.TOP, Side.BOTTOM):  # monitors left/right of the PC screen
                fy = model.y / model.cal.height
                y = target.top + round(fy * (target.height - 1))
                x = target.right - 1 - RETURN_GAP_PX if exit is Exit.BEFORE else target.left + RETURN_GAP_PX
            else:  # monitors above/below the PC screen
                fx = model.x / model.cal.width
                x = target.left + round(fx * (target.width - 1))
                y = target.bottom - 1 - RETURN_GAP_PX if exit is Exit.BEFORE else target.top + RETURN_GAP_PX
            log.info("Switched to the %s monitor", "first side" if exit is Exit.BEFORE else "second side")
        self.screen.set_cursor_pos(x, y)
        self._changed()

    def _new_model(self, mon: Rect) -> PointerModel:
        model = PointerModel(self.config.calibration, self.config.side, self.config.return_margin,
                             away_factor=self.config.away_factor, side_margin=self.config.side_margin,
                             side_away_factor=self.config.side_away_factor)
        self.side_monitors = self._side_monitors(mon)
        model.exit_before = self.side_monitors[0] is not None
        model.exit_after = self.side_monitors[1] is not None
        return model

    def _side_monitors(self, mon: Rect) -> tuple[Rect | None, Rect | None]:
        """Monitors touching the PC screen on the tablet's two side edges (left/right if the
        tablet is above or below the screen, above/below if it is beside it)."""
        if not self.config.side_exits:
            return None, None
        steps = [i / 8 for i in range(9)]

        def first(points) -> Rect | None:
            return next((m for x, y in points if (m := self.screen.monitor_at(x, y)) is not None), None)

        if self.config.side in (Side.TOP, Side.BOTTOM):
            ys = [mon.top + round(f * (mon.height - 1)) for f in steps]
            return first((mon.left - 1, y) for y in ys), first((mon.right, y) for y in ys)
        xs = [mon.left + round(f * (mon.width - 1)) for f in steps]
        return first((x, mon.top - 1) for x in xs), first((x, mon.bottom) for x in xs)

    def _switch(self, mode: Mode, mon: Rect) -> None:
        self.mode = mode
        self.entry_monitor = mon
        self.cal_phase = 0
        self.keyboard.clear()
        self.forwarded_keys.clear()
        self.ignored_buttons = self.phys_buttons
        self.edge.reset()
        self._frac = [0.0, 0.0]
        self._wheel = [0, 0]

    def _require_connection(self) -> bool:
        if self.link.connected:
            return True
        now = self.clock()
        if now - self._last_offline_notice > 10:
            self._last_offline_notice = now
            self.notify("Tablet non connesso: controlla che il Bluetooth sia attivo e che sia abbinato.")
        return False

    def _wake_and_unlock(self) -> None:
        """Wakes the tablet; if it has been idle, also dismisses a swipe (non-PIN) lock screen.

        Without a channel back from the tablet BlueShare cannot know whether it is locked, so the
        unlock is only sent after ``unlock_after_idle_s`` without input, when the screen has
        probably turned off. Sending it to an unlocked tablet could open a menu or scroll a page.
        """
        idle = self.clock() - self._last_remote_input >= self.config.unlock_after_idle_s
        self._last_remote_input = self.clock()
        if not self.config.wake_on_enter:
            return
        self.link.send_system(hid.system_report(hid.SYSTEM_WAKE))
        self.link.send_system(hid.system_report(0))
        method = self.config.unlock_method
        if not idle or method not in ("menu", "swipe"):
            return
        self.link.pause(0.6)  # let the screen turn on
        log.info("Dismissing the lock screen (%s)", method)
        if method == "menu":
            self.link.send_keyboard(bytes([0, 0, MENU_USAGE, 0, 0, 0, 0, 0]))
            self.link.send_keyboard(bytes(8))
            self.link.pause(0.4)
            return
        cal = self.config.calibration
        height = cal.height if cal.is_valid() else 1000
        # drag up from the bottom centre, like a finger swipe
        self.link.move_exact(-PIN_COUNTS, PIN_COUNTS, SETTLE_S)
        self.link.move_exact(cal.width // 2 if cal.is_valid() else 500, -height // 20, SETTLE_S)
        self.link.move_exact(0, 0, 0.05, buttons=hid.MOUSE_LEFT)
        for _ in range(12):
            self.link.move_exact(0, -height // 24, 0.015, buttons=hid.MOUSE_LEFT)
        self.link.move_exact(0, 0, 0.5, buttons=0)

    def _track_buttons(self, flags: int) -> int:
        """Updates held buttons; returns the HID bits that were just pressed."""
        pressed = 0
        for down, up, bit in _BUTTONS:
            if flags & down:
                self.phys_buttons |= bit
                pressed |= bit
            if flags & up:
                self.phys_buttons &= ~bit
                self.ignored_buttons &= ~bit
        return pressed

    def _scale(self, dx: int, dy: int) -> tuple[int, int]:
        speed = self.config.pointer_speed
        out = []
        for axis, delta in enumerate((dx, dy)):
            value = self._frac[axis] + delta * speed
            whole = int(value)
            self._frac[axis] = value - whole
            out.append(whole)
        return out[0], out[1]

    def _wheel_steps(self, flags: int, data: int) -> tuple[int, int]:
        steps = [0, 0]
        for axis, flag in enumerate((RI_MOUSE_WHEEL, RI_MOUSE_HWHEEL)):
            if flags & flag:
                self._wheel[axis] += data
                steps[axis] = int(self._wheel[axis] / WHEEL_DELTA)
                self._wheel[axis] -= steps[axis] * WHEEL_DELTA
        return steps[0], steps[1]

    def _changed(self) -> None:
        if self.on_state_change:
            try:
                self.on_state_change()
            except Exception:
                log.exception("state change listener failed")
