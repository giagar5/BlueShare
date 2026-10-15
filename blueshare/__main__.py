"""BlueShare entry point: python -m blueshare"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import APP_DIR, CONFIG_PATH, Config
from .controller import Controller, Mode
from .tray import PresetMenu, Tray
from .winput import InputThread, WindowsScreen, acquire_single_instance, enable_dpi_awareness

log = logging.getLogger("blueshare")

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BlueShare"

# Same level names for both directions, from the most deliberate to the most eager.
# Ritorno su Windows: (away_factor, return_margin). Lower values return sooner but may return early
# after fast moves away from the edge (Android acceleration); a negative margin returns before the edge.
RETURN_PRESETS = {
    "Bilanciato": (2.4, 0.04),
    "Rapido": (1.8, 0.03),
    "Aggressivo": (1.3, 0.02),
    "Immediato": (1.0, 0.0),
    "Anticipato": (1.0, -0.03),
}

# Passaggio al tablet: (push past the PC screen edge in raw mouse counts, zone before the edge in px).
ENTRY_PRESETS = {
    "Bilanciato": (40, 0),
    "Rapido": (15, 0),
    "Aggressivo": (5, 0),
    "Immediato": (1, 0),
    "Anticipato": (1, 6),
}

# Passaggio ai monitor laterali: same levels as the return (applied to the tablet's side edges), or off.
SIDE_OFF = "Disattivato"


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_DIR / "blueshare.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "BlueShare", 0x10)


def main() -> int:
    enable_dpi_awareness()
    mutex = acquire_single_instance()
    if mutex is None:
        _message_box("BlueShare è già in esecuzione (icona nell'area di notifica).")
        return 1
    _setup_logging()
    log.info("BlueShare %s starting", __version__)

    from .ble import BleHidDevice  # WinRT import is slow; keep it after the single-instance check

    config = Config.load()
    tray = Tray()
    tray.settings_dir = str(APP_DIR)
    APP_DIR.mkdir(parents=True, exist_ok=True)

    def save_config() -> None:
        config.save(CONFIG_PATH)

    def on_trusted_host(host_id: str) -> None:
        config.trusted_host_id = host_id
        save_config()
        tray.notify("Tablet abbinato: da ora tastiera e mouse vengono inviati solo a questo dispositivo.")

    controller: Controller | None = None
    ble = BleHidDevice(config.trusted_host_id, on_trusted_host=on_trusted_host,
                       on_connection_change=lambda c: controller and controller.on_connection_change(c))
    controller = Controller(config, ble, WindowsScreen(), notify=tray.notify, save_config=save_config)
    input_thread = InputThread(controller.on_raw_mouse, controller.on_key, controller.on_mouse_hook)

    def status() -> tuple[str, str, bool]:
        if controller.mode is Mode.CALIBRATING:
            return "remote", "Calibrazione in corso", True
        if controller.mode is Mode.REMOTE:
            return "remote", "Controlli il tablet", True
        if ble.connected:
            return "local", "Tablet connesso", False
        return "offline", "In attesa del tablet", False

    def forget() -> None:
        config.trusted_host_id = ""
        save_config()
        ble.forget_host()
        tray.notify("Tablet dimenticato: il prossimo dispositivo che si collega verrà abbinato.")

    tray.status = status
    tray.on_toggle = controller.toggle
    tray.on_calibrate = controller.start_calibration
    tray.on_forget = forget

    def set_entry(name: str) -> None:
        config.entry_push_counts, config.entry_zone_px = ENTRY_PRESETS[name]
        controller.edge.threshold = config.entry_push_counts
        save_config()
        log.info("Entry preset: %s", name)
        tray.refresh()

    def set_return(name: str) -> None:
        config.away_factor, config.return_margin = RETURN_PRESETS[name]
        save_config()
        log.info("Return preset: %s", name)
        tray.refresh()

    def set_side(name: str) -> None:
        config.side_exits = name != SIDE_OFF
        if config.side_exits:
            config.side_away_factor, config.side_margin = RETURN_PRESETS[name]
        save_config()
        log.info("Side preset: %s", name)
        tray.refresh()

    def current_side() -> str:
        if not config.side_exits:
            return SIDE_OFF
        return next((n for n, v in RETURN_PRESETS.items() if v == (config.side_away_factor, config.side_margin)), "")

    tray.preset_menus = [
        PresetMenu("Passaggio al tablet", list(ENTRY_PRESETS),
                   lambda: next((n for n, v in ENTRY_PRESETS.items()
                                 if v == (config.entry_push_counts, config.entry_zone_px)), ""),
                   set_entry),
        PresetMenu("Ritorno su Windows", list(RETURN_PRESETS),
                   lambda: next((n for n, v in RETURN_PRESETS.items()
                                 if v == (config.away_factor, config.return_margin)), ""),
                   set_return),
        PresetMenu("Passaggio ai monitor laterali", [*RETURN_PRESETS, SIDE_OFF], current_side, set_side),
    ]
    controller.on_state_change = tray.refresh

    started = []

    def setup() -> None:
        try:
            ble.start()
            started.append(ble)
            input_thread.start_and_wait()
            started.append(input_thread)
        except Exception as e:
            log.exception("Startup failed")
            _message_box(f"Avvio non riuscito: {e}")
            tray.icon.stop()
            return
        tray.refresh()
        if not config.calibration.is_valid():
            tray.notify("Pronto. Abbina il tablet via Bluetooth; al primo passaggio partirà la calibrazione.")
        else:
            tray.notify("Pronto. Spingi il cursore oltre il bordo per passare al tablet (Ctrl+Alt+S).")

    try:
        tray.run(setup)
    finally:
        log.info("Shutting down")
        if input_thread in started:
            input_thread.stop()
        if ble in started:
            ble.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
