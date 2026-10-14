"""Notification-area icon and menu."""
from __future__ import annotations

import logging
import os
from typing import Callable

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

_COLORS = {"offline": (128, 128, 128), "local": (0, 120, 215), "remote": (16, 160, 80)}


def _icon_image(state: str) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=_COLORS[state])
    draw.rectangle((16, 14, 48, 34), outline="white", width=4)     # tablet
    draw.rectangle((12, 40, 52, 50), fill="white")                 # keyboard
    return image


class Tray:
    def __init__(self, title: str = "BlueShare") -> None:
        self.title = title
        self.status: Callable[[], tuple[str, str, bool]] = lambda: ("offline", "Avvio...", False)
        self.on_toggle: Callable[[], None] = lambda: None
        self.on_calibrate: Callable[[], None] = lambda: None
        self.on_forget: Callable[[], None] = lambda: None
        self.settings_dir = ""
        self.icon = pystray.Icon("BlueShare", _icon_image("offline"), title, menu=self._menu())

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self.status()[1], None, enabled=False),
            pystray.Menu.SEPARATOR,
            item(lambda _: "Torna al PC" if self.status()[2] else "Passa al tablet",
                 lambda: self.on_toggle(), default=True),
            item("Calibra...", lambda: self.on_calibrate()),
            item("Dimentica il tablet abbinato", lambda: self.on_forget()),
            item("Apri cartella impostazioni", lambda: os.startfile(self.settings_dir)),
            pystray.Menu.SEPARATOR,
            item("Esci", lambda: self.icon.stop()),
        )

    def refresh(self) -> None:
        state, text, _ = self.status()
        self.icon.icon = _icon_image(state)
        self.icon.title = f"{self.title} - {text}"
        self.icon.update_menu()

    def notify(self, message: str) -> None:
        log.info("Notification: %s", message)
        try:
            self.icon.notify(message, self.title)
        except Exception:
            log.debug("notification not shown", exc_info=True)

    def run(self, setup: Callable[[], None]) -> None:
        def _setup(icon: pystray.Icon) -> None:
            icon.visible = True
            setup()
        self.icon.run(setup=_setup)
