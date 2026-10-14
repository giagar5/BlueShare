"""Estimate the tablet cursor position from the relative movements BlueShare sends.

Coordinates are in HID counts. The tablet clamps its cursor at the screen borders, and the
model mirrors that. The calibration measures the screen size in counts at a slow, unaccelerated
speed, i.e. at the minimum gain. Android may accelerate faster movements, so the model is
pessimistic on the axis that leads back to the PC: movements away from the return edge are
multiplied by ``away_factor`` (the worst-case acceleration), movements towards it are not. The
estimate therefore never reaches the return edge before the real cursor does.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

PIN_COUNTS = 32767  # one HID report this large drives the cursor into a corner
SETTLE_S = 0.15     # pause after a pin so Android's velocity tracker forgets it (no acceleration)


class Side(str, Enum):
    """Where the tablet sits relative to the PC screen."""
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class Calibration:
    width: int
    height: int

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0


class PointerModel:
    def __init__(self, calibration: Calibration, side: Side, return_margin: float = 0.1,
                 away_factor: float = 3.0, overshoot_reset_s: float = 0.4, entry_inset: float = 0.02) -> None:
        self.cal = calibration
        self.side = Side(side)
        self.return_margin = return_margin
        self.away_factor = max(1.0, away_factor)
        self.overshoot_reset_s = overshoot_reset_s
        self.entry_inset = entry_inset
        self.x = 0.0
        self.y = 0.0
        self.overshoot = 0.0
        self._last_push = 0.0

    @property
    def _return_extent(self) -> int:
        return self.cal.height if self.side in (Side.TOP, Side.BOTTOM) else self.cal.width

    @property
    def margin_counts(self) -> int:
        return max(1, round(self.return_margin * self._return_extent))

    @property
    def lead_counts(self) -> int:
        """With a negative margin: how far before the edge the return already triggers."""
        return max(0, round(-self.return_margin * self._return_extent))

    def enter(self, fraction: float) -> list[tuple[int, int, float]]:
        """Place the cursor just inside the edge facing the PC.

        Returns (dx, dy, settle_s) moves: pin into the corner, wait, move along the edge, wait.
        Each move is alone in Android's velocity window, so none of them is accelerated.
        """
        fraction = min(1.0, max(0.0, fraction))
        w, h = self.cal.width, self.cal.height
        self.overshoot = 0.0
        if self.side is Side.TOP:  # enter through the tablet's bottom edge
            inset = max(1, round(self.entry_inset * h))
            self.x, self.y = round(fraction * w), h - inset
            pin, along = (-PIN_COUNTS, PIN_COUNTS), (round(self.x), -inset)
        elif self.side is Side.BOTTOM:
            inset = max(1, round(self.entry_inset * h))
            self.x, self.y = round(fraction * w), inset
            pin, along = (-PIN_COUNTS, -PIN_COUNTS), (round(self.x), inset)
        elif self.side is Side.LEFT:  # enter through the tablet's right edge
            inset = max(1, round(self.entry_inset * w))
            self.x, self.y = w - inset, round(fraction * h)
            pin, along = (PIN_COUNTS, -PIN_COUNTS), (-inset, round(self.y))
        else:
            inset = max(1, round(self.entry_inset * w))
            self.x, self.y = inset, round(fraction * h)
            pin, along = (-PIN_COUNTS, -PIN_COUNTS), (inset, round(self.y))
        return [(*pin, SETTLE_S), (*along, SETTLE_S)]

    def fraction_along_edge(self) -> float:
        if self.side in (Side.TOP, Side.BOTTOM):
            return self.x / self.cal.width
        return self.y / self.cal.height

    def _pessimistic(self, dx: float, dy: float) -> tuple[float, float]:
        f = self.away_factor
        if self.side is Side.TOP and dy < 0 or self.side is Side.BOTTOM and dy > 0:
            dy *= f
        elif self.side is Side.LEFT and dx < 0 or self.side is Side.RIGHT and dx > 0:
            dx *= f
        return dx, dy

    def move(self, dx: int, dy: int, now: float) -> bool:
        """Apply a move; returns True when the user pushed past the PC-facing edge by the margin."""
        w, h = self.cal.width, self.cal.height
        ex, ey = self._pessimistic(dx, dy)
        nx, ny = self.x + ex, self.y + ey
        beyond = {
            Side.TOP: ny - h,
            Side.BOTTOM: -ny,
            Side.LEFT: nx - w,
            Side.RIGHT: -nx,
        }[self.side]
        at_edge = {
            Side.TOP: self.y >= h,
            Side.BOTTOM: self.y <= 0,
            Side.LEFT: self.x >= w,
            Side.RIGHT: self.x <= 0,
        }[self.side]
        self.x = min(w, max(0, nx))
        self.y = min(h, max(0, ny))

        lead = self.lead_counts
        if lead:
            toward, distance = {
                Side.TOP: (dy, h - self.y),
                Side.BOTTOM: (-dy, self.y),
                Side.LEFT: (dx, w - self.x),
                Side.RIGHT: (-dx, self.x),
            }[self.side]
            return toward > 0 and distance <= lead

        if beyond > 0:
            keep = at_edge and now - self._last_push <= self.overshoot_reset_s
            self.overshoot = (self.overshoot if keep else 0) + beyond
            self._last_push = now
        elif beyond < 0 or not at_edge:
            self.overshoot = 0
        return self.overshoot > self.margin_counts


class EdgePush:
    """Detects a deliberate push against a PC screen edge (not just touching it)."""

    def __init__(self, threshold_counts: int, reset_s: float = 0.5) -> None:
        self.threshold = threshold_counts
        self.reset_s = reset_s
        self.pushed = 0
        self._last = 0.0

    def reset(self) -> None:
        self.pushed = 0

    def feed(self, at_edge: bool, toward_edge: int, now: float) -> bool:
        """toward_edge: movement component towards the edge (positive = pushing).

        Small movements away from the edge (mouse jitter) do not reset the count: moving away for
        real takes the cursor off the edge, and that does.
        """
        if not at_edge or now - self._last > self.reset_s:
            self.pushed = 0
        self._last = now
        if at_edge and toward_edge > 0:
            self.pushed += toward_edge
        return self.pushed >= self.threshold
