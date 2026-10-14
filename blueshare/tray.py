"""Notification-area icon and menu."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

_COLORS = {"offline": (128, 128, 128), "local": (0, 120, 215), "remote": (16, 160, 80)}
MAX_PRESETS = 6       # entries per submenu; unused ones are hidden
MAX_PRESET_MENUS = 3  # preset submenus; unused ones are hidden


def _icon_image(state: str) -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=_COLORS[state])
    draw.rectangle((16, 14, 48, 34), outline="white", width=4)     # tablet
    draw.rectangle((12, 40, 52, 50), fill="white")                 # keyboard
    return image


@dataclass
class PresetMenu:
    """A radio-button submenu: pick one named preset."""
    title: str
    names: list[str]
    current: Callable[[], str]
    select: Callable[[str], None]


class Tray:
    def __init__(self, title: str = "BlueShare") -> None:
        self.title = title
        self.status: Callable[[], tuple[str, str, bool]] = lambda: ("offline", "Avvio...", False)
        self.on_toggle: Callable[[], None] = lambda: None
        self.on_calibrate: Callable[[], None] = lambda: None
        self.on_forget: Callable[[], None] = lambda: None
        self.preset_menus: list[PresetMenu] = []
        self.settings_dir = ""
        self.icon = pystray.Icon("BlueShare", _icon_image("offline"), title, menu=self._menu())

    def _preset_menu(self, slot: int) -> pystray.MenuItem:
        def group() -> PresetMenu | None:
            return self.preset_menus[slot] if slot < len(self.preset_menus) else None

        def entry(index: int) -> pystray.MenuItem:
            def name() -> str:
                g = group()
                return g.names[index] if g and index < len(g.names) else ""
            return pystray.MenuItem(
                lambda _: name(),
                lambda: group().select(name()),
                checked=lambda _: bool(name()) and group().current() == name(),
                radio=True,
                visible=lambda _: bool(name()),
            )

        return pystray.MenuItem(
            lambda _: group().title if group() else "",
            pystray.Menu(*(entry(i) for i in range(MAX_PRESETS))),
            visible=lambda _: group() is not None,
        )

    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: self.status()[1], None, enabled=False),
            pystray.Menu.SEPARATOR,
            item(lambda _: "Torna al PC" if self.status()[2] else "Passa al tablet",
                 lambda: self.on_toggle(), default=True),
            item("Calibra...", lambda: self.on_calibrate()),
            *(self._preset_menu(slot) for slot in range(MAX_PRESET_MENUS)),
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
