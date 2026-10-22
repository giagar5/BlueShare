# BlueShare

Use your Windows PC keyboard and mouse on an Android tablet, as if the tablet were a second
screen next to your monitor: push the cursor past the screen edge and it continues on the tablet,
which keeps running Android. Push it back past the tablet edge to return to Windows.

BlueShare makes Windows itself act as a **Bluetooth keyboard and mouse** (HID over GATT).
Nothing has to be installed on the tablet, and **USB debugging / ADB is not needed**, so it also
works on devices where developer options are forbidden by company policy.

It is derived from [InputShare](https://github.com/InputShare/InputShare), which does the same over
ADB (see [NOTICE](NOTICE)).

## Requirements

- Windows 10/11 with a Bluetooth adapter that supports the LE *peripheral* role (most recent laptops do)
- An Android tablet (tested on Android 16)
- Python 3.12 and [uv](https://docs.astral.sh/uv/)

## Setup

```powershell
git clone https://github.com/giagar5/BlueShare.git
cd BlueShare
uv sync
uv run blueshare
```

1. Start BlueShare: an icon appears in the notification area.
2. On the tablet open **Settings › Connected devices › Pair new device** and pick your PC.
   The first device that pairs becomes the *trusted* tablet: input reports are sent only to it.
4. Push the cursor past the top edge of the screen. The first time, an automatic calibration starts:
   the tablet cursor moves slowly down by itself; click (or press Enter) as soon as it touches the
   bottom edge, then again when it touches the right edge.
5. In the tray menu › *Layout tastiera del tablet* pick the layout the tablet uses for the BlueShare
   keyboard (Settings › System › Keyboard › Physical keyboard; Italian by default). BlueShare then
   translates every key so that you type with the layout active on the PC, whatever the tablet uses.

## Usage

| Action | How |
| --- | --- |
| Go to the tablet | push the cursor past the screen edge (touching it is not enough) |
| Back to Windows | push the cursor past the tablet edge facing the PC |
| Straight to a side monitor | push past the tablet's left/right edge: the cursor lands on the monitor on that side of the PC screen; tune or turn off in tray menu › *Passaggio ai monitor laterali* |
| Switch manually | `Ctrl` + `Alt` + `S`, or the tray menu |
| Go to the tablet more or less easily | tray menu › *Passaggio al tablet* |
| Return sooner or later | tray menu › *Ritorno su Windows* |
| Recalibrate, forget the tablet, quit | tray menu |

While you are on the tablet, Windows receives no keyboard or mouse input. Entering the tablet
also sends a *wake up* key, so a sleeping tablet turns its screen on; if the tablet received no
input for a while, BlueShare also dismisses a swipe lock screen (never a PIN or pattern).

## Settings

`%APPDATA%\BlueShare\config.json` (restart BlueShare after editing):

| Key | Default | Meaning |
| --- | --- | --- |
| `tablet_side` | `top` | where the tablet is relative to the PC screen: `top`, `bottom`, `left`, `right` |
| `pointer_speed` | `1.0` | multiplier from mouse counts to counts sent to the tablet |
| `entry_push_counts` | `15` | how far (raw mouse counts) you must push against the edge to switch |
| `entry_zone_px` | `0` | if > 0, the push already counts this many pixels before the edge |
| `return_margin` | `0.04` | how far past the tablet edge (fraction of its size) you must push to come back; negative = come back that far *before* the edge |
| `away_factor` | `2.4` | worst-case Android pointer acceleration assumed when estimating the return (see below) |
| `calibration_reaction_s` | `0.25` | reaction time subtracted from the calibration (the click comes after the edge is reached) |
| `wake_on_enter` | `true` | send a wake-up key when switching to the tablet |
| `side_exits` | `true` | leaving the tablet by a side edge goes to the monitor on that side of the PC screen (if there is one) |
| `side_away_factor`, `side_margin` | `2.4`, `0.04` | like `away_factor` and `return_margin`, for the side edges |
| `keyboard_layout` | `00000410` | Windows layout id matching the tablet's layout (Italian); `""` sends keys by physical position |
| `unlock_method` | `menu` | how to dismiss a swipe lock screen: `menu` (Menu key), `swipe` (mouse drag) or `none` |
| `unlock_after_idle_s` | `30` | only unlock if the tablet received no input for this many seconds |

Logs: `%LOCALAPPDATA%\BlueShare\blueshare.log`.

## How it works

- `ble.py` publishes a HID-over-GATT service (keyboard, mouse, system control) with the WinRT
  `GattServiceProvider` API and notifies input reports to the trusted host only.
- `winput.py` reads relative mouse motion through Raw Input and installs low-level keyboard/mouse
  hooks that block input to Windows while it is forwarded (Raw Input keeps flowing when the hook
  blocks an event).
- `pointer.py` mirrors the tablet cursor: on entry it pins the cursor into a corner and moves it to
  the matching point of the edge (with pauses, so Android does not accelerate those jumps), then
  follows the counts it sends, clamping at the borders like Android does. It keeps a separate
  pessimistic estimate for each exit edge: the one facing the PC and the two side edges.
- `keyboard_layout.py` makes the PC layout win: for every key it asks Windows which character the
  layout of the foreground window types (so switching layouts with Win+Space just works), and sends
  the key and modifiers that type that character on the tablet's layout. Shortcuts keep the user's
  modifiers; Caps Lock is applied on the PC side. Characters missing from the tablet layout (`~` and
  `` ` `` on Italian) fall back to the physical key.
- `ble.py` also watches the real Bluetooth link to the tablet: Windows does not always notice when
  the tablet drops the connection, and BlueShare would otherwise forward input into the void.

Android accelerates fast mouse movements and many devices have no setting to turn that off, so
counts do not map linearly to pixels. The calibration therefore measures the screen at a slow,
unaccelerated speed, and the estimate is deliberately pessimistic: movements *away* from the edge
that leads back to Windows count `away_factor` times. The estimate never reaches that edge before
the real cursor does; at worst you push a little further to come back, but you are never thrown
back to Windows from the middle of the tablet screen.

Both directions can be tuned from the tray menu with the same five levels:

| Level | *Passaggio al tablet* | *Ritorno su Windows* |
| --- | --- | --- |
| Bilanciato | push 40 counts past the edge | factor 2.4, push 4% past the edge |
| Rapido | push 15 counts (default) | factor 1.8, push 3% |
| Aggressivo | push 5 counts | factor 1.3, push 2% |
| Immediato | any push at the edge | factor 1.0, as soon as the estimate reaches the edge |
| Anticipato | any move towards the edge within 6 px of it | factor 1.0, 3% before the edge when heading there |

The eager levels switch with less effort but more often by accident; the return ones can come
back before the cursor reaches the tablet edge after fast movements away from it. The side edges
(*Passaggio ai monitor laterali*) offer the same levels with the *Ritorno su Windows* values, plus
*Disattivato*.

## Limitations

- The return to Windows is estimated: after large fast movements away from the edge you may need
  to push further than usual. Recalibrate from the tray menu if it feels off.
- No clipboard sharing and no automatic return when the tablet screen turns off: Android sends no
  signal to a Bluetooth keyboard/mouse when the screen turns off, and there is no other channel
  from the tablet back to the PC.
- BlueShare cannot tell whether the tablet is locked; the unlock is only sent after a period of
  inactivity (`unlock_after_idle_s`), when the screen has probably turned off.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
