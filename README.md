# countdown_bar

A graphical countdown timer that renders a sixel progress bar directly in the terminal.

![dark background, large centred message, shrinking colour-coded bar]

## Usage

```
./countdown_bar.py <seconds|hh:mm> <message>
```

| Argument | Description |
|---|---|
| `seconds` | Duration as a positive integer, e.g. `120` |
| `hh:mm` | Target wall-clock time, e.g. `14:30`. Rolls to tomorrow if the time has already passed today. |
| `message` | Text displayed centred in large font |

### Examples

```sh
./countdown_bar.py 120 "Coffee Break"
./countdown_bar.py 25:00 "Pomodoro"        # interpreted as 25 minutes (1500 s)
./countdown_bar.py 14:30 "Stand-up"        # counts to 14:30 today
./countdown_bar.py 9:00  "Morning meeting" # rolls to tomorrow if past 09:00
```

Press **q**, **Esc**, or **Ctrl-C** at any time to exit; the cursor and terminal state are restored on exit.

## What it looks like

```
┌─────────────────────────────────────────────────────────┐
│  Now: 14:22:10                          End: 14:24:10   │
│                                                         │
│               Coffee Break                              │  ← 58 pt bold
│                                                         │
│                 01:53 remaining                         │
│  ████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │  ← colour-coded bar
└─────────────────────────────────────────────────────────┘
```

The bar shrinks from right to left as time runs out. Its colour shifts automatically:

| Remaining | Colour |
|---|---|
| > 50 % | Green |
| 26 – 50 % | Amber |
| 11 – 25 % | Orange |
| ≤ 10 % | Red |

The image is centred in the terminal window both horizontally and vertically. Cell pixel dimensions are queried from the terminal via `CSI 16 t` (XTWINOPS) for accurate placement; falls back to 8 × 16 px per cell if the terminal does not respond.

Resizing the terminal window (`SIGWINCH`) clears the screen and redraws the image centred in the new dimensions at the next tick.

## Requirements

### Python

Python 3.10+ and **Pillow**:

```sh
pip install pillow
```

Pillow is used both for image composition (rounded rectangles, TrueType text) and for colour quantisation before sixel encoding. No other third-party packages are needed; the sixel encoder is built into the script.

### Fonts

The script looks for **DejaVu Sans** and **DejaVu Sans Mono** (preferred) or **Liberation Sans / Mono** as fallbacks, then falls back to Pillow's built-in bitmap font.

```sh
# Fedora / RHEL
dnf install dejavu-fonts-all

# Debian / Ubuntu
apt install fonts-dejavu
```

### Terminal

The terminal must support **DEC sixel graphics**. Tested terminals:

| Terminal | Sixel support |
|---|---|
| WezTerm | ✓ built-in |
| foot | ✓ built-in |
| mlterm | ✓ built-in |
| xterm | ✓ launch with `xterm -ti vt340` |
| iTerm2 (macOS) | ✓ built-in |
| GNOME Terminal / Konsole | ✗ no sixel |

You can verify sixel support by running:

```sh
printf '\033[c'
```

Look for `?4` in the response (capability 4 = sixel graphics).

## How it works

1. **Image composition** — Pillow draws an 800 × 290 px frame into an in-memory `Image`: background fill, rounded-rectangle bar track, rounded-rectangle fill, TrueType text layers.
2. **Sixel encoding** — the frame is colour-quantised to ≤ 128 colours (median-cut, no dither) then encoded as a DCS sixel string with per-band run-length compression.
3. **Timing** — after each render the script sleeps until the exact wall-clock second when the remaining count decrements, keeping the display accurate regardless of render time.
4. **Keypress handling** — the terminal is put into raw mode so `q`, `Esc`, and `Ctrl-C` are detected immediately without waiting for Enter. `select()` is used during the sleep so the process wakes on input rather than polling. The original terminal state is restored unconditionally on exit.
5. **Centering** — terminal cell size is queried once at startup; the cursor is positioned at `\033[row;colH` before every frame. A `SIGWINCH` handler sets a flag that triggers a screen clear and position recalculation on the next tick.
