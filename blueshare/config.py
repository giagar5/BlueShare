"""Persistent settings stored as JSON in %APPDATA%\\BlueShare."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .pointer import Calibration, Side

log = logging.getLogger(__name__)

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "BlueShare"
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class Config:
    tablet_side: str = Side.TOP.value
    pointer_speed: float = 1.0       # HID counts sent per raw mouse count
    entry_push_counts: int = 15      # raw mouse counts pushed against the edge to switch to the tablet
    entry_zone_px: int = 0           # > 0: the push counts from this many pixels before the edge
    return_margin: float = 0.04      # fraction of the tablet extent to push past its edge to come back;
                                     # negative: come back that far before reaching the edge
    away_factor: float = 2.4         # worst-case Android acceleration assumed for moves away from that edge
    calibration_reaction_s: float = 0.25  # typical delay between the cursor touching the edge and the click
    wake_on_enter: bool = True
    keyboard_layout: str = "00000410"  # layout set on the tablet (Windows KLID); "" = send keys by position
    paste_gap_ms: float = 8          # pause after each pasted character; raise it if characters get lost
    side_exits: bool = True          # leaving the tablet by a side edge goes to the monitor on that side
    side_away_factor: float = 2.4    # like away_factor and return_margin, for the two side edges
    side_margin: float = 0.04
    unlock_method: str = "menu"      # dismiss a swipe lock screen on entry: "menu", "swipe" or "none"
    unlock_after_idle_s: float = 30  # only unlock if the tablet received no input for this long
    calibration_width: int = 0
    calibration_height: int = 0
    trusted_host_id: str = ""        # Bluetooth device id of the paired tablet; reports go only there

    @property
    def calibration(self) -> Calibration:
        return Calibration(self.calibration_width, self.calibration_height)

    @property
    def side(self) -> Side:
        return Side(self.tablet_side)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("Cannot read %s (%s); using defaults", path, e)
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        values = {k: v for k, v in raw.items() if k in known}
        try:
            config = cls(**values)
            Side(config.tablet_side)
        except (TypeError, ValueError) as e:
            log.error("Invalid settings in %s (%s); using defaults", path, e)
            return cls()
        return config

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)
