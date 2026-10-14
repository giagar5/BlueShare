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
    entry_push_counts: int = 150     # raw mouse counts pushed against the edge to switch to the tablet
    return_margin: float = 0.1       # fraction of the tablet extent to push past its edge to come back
    away_factor: float = 3.0         # worst-case Android acceleration assumed for moves away from that edge
    wake_on_enter: bool = True
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
