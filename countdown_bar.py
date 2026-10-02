#!/usr/bin/env python3
"""Graphical sixel countdown progress bar.

Usage: ./countdown_bar.py <seconds> <message>

Requires: Pillow  (pip install pillow)
          A sixel-capable terminal: WezTerm, foot, xterm -ti vt340, mlterm, iTerm2
"""

import os
import re
import select
import signal
import sys
import termios
import time
import tty
from datetime import datetime

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Pillow not installed. Run: pip install pillow", file=sys.stderr)
    sys.exit(1)

# ── Geometry ──────────────────────────────────────────────────────────────────
W, H    = 800, 290
BAR_X   = 40
BAR_W   = 720
BAR_H   = 44
BAR_Y   = 232
CORNER  = 10

# ── Palette ───────────────────────────────────────────────────────────────────
C_BG        = ( 13,  17,  23)
C_TRACK     = ( 30,  45,  61)
C_TEXT_DIM  = (139, 148, 158)
C_TEXT_HI   = (205, 217, 229)
C_TEXT_SOFT = ( 87,  96, 106)

def bar_color(pct: float) -> tuple:
    if pct <= 0.10: return (214,  48,  49)   # red
    if pct <= 0.25: return (225, 112,  85)   # orange
    if pct <= 0.50: return (253, 203, 110)   # amber
    return (0, 184, 148)                      # green


# ── Font loading ──────────────────────────────────────────────────────────────
def _first_font(paths: list[str], size: int) -> ImageFont.FreeTypeFont:
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default(size=size)

def load_fonts() -> dict:
    bold = [
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    reg = [
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    mono = [
        "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/liberation-mono/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    return {
        'big':    _first_font(bold, 58),
        'medium': _first_font(mono, 22),
        'small':  _first_font(reg,  20),
    }


# ── Sixel encoder ─────────────────────────────────────────────────────────────
def to_sixel(img: Image.Image, max_colors: int = 128) -> str:
    """Encode a PIL RGB image as a DCS sixel string."""
    # Quantise to bound the palette size and improve RLE compression
    img_q   = img.quantize(colors=max_colors, dither=Image.Dither.NONE)
    img_rgb = img_q.convert('RGB')
    width, height = img_rgb.size
    flat    = list(img_rgb.get_flattened_data())   # (r,g,b) per pixel, row-major

    # Build colour map
    cmap  = {}   # (r,g,b) -> index
    clist = []   # index   -> (r,g,b)
    pidx  = []   # pixel position -> colour index
    for rgb in flat:
        if rgb not in cmap:
            cmap[rgb] = len(clist)
            clist.append(rgb)
        pidx.append(cmap[rgb])

    parts = ['\033Pq', '"1;1;{};{}'.format(width, height)]

    # Palette definitions
    for i, (r, g, b) in enumerate(clist):
        parts.append('#{};2;{};{};{}'.format(
            i, r * 100 // 255, g * 100 // 255, b * 100 // 255))

    # Sixel bands (6 rows each)
    for band_y in range(0, height, 6):
        rows = min(6, height - band_y)
        # bdata[colour_idx] = list of per-column bitmasks
        bdata: dict[int, list[int]] = {}
        for dy in range(rows):
            row_off = (band_y + dy) * width
            bit = 1 << dy
            for x in range(width):
                ci = pidx[row_off + x]
                if ci not in bdata:
                    bdata[ci] = [0] * width
                bdata[ci][x] |= bit

        first = True
        for ci, masks in bdata.items():
            if not first:
                parts.append('$')       # carriage-return within band
            first = False
            parts.append('#{}'.format(ci))
            # Run-length encode this colour's row
            i = 0
            while i < width:
                v = masks[i]
                j = i + 1
                while j < width and masks[j] == v:
                    j += 1
                run  = j - i
                char = chr(63 + v)
                parts.append('!{}{}'.format(run, char) if run > 3 else char * run)
                i = j

        parts.append('-')   # next band

    parts.append('\033\\')  # ST – string terminator
    return ''.join(parts)


# ── Frame renderer ────────────────────────────────────────────────────────────
def draw_frame(img: Image.Image, draw: ImageDraw.ImageDraw, fonts: dict,
               rem: int, cur: str, end: str, duration: int, message: str) -> str:
    draw.rectangle((0, 0, W, H), fill=C_BG)

    # Bar track
    draw.rounded_rectangle((BAR_X, BAR_Y, BAR_X + BAR_W, BAR_Y + BAR_H),
                            radius=CORNER, fill=C_TRACK)

    # Bar fill – shrinks from right as time counts down
    pct = rem / duration
    fill_w = int(pct * BAR_W)
    if fill_w > 0:
        r = min(CORNER, fill_w // 2, BAR_H // 2)
        draw.rounded_rectangle((BAR_X, BAR_Y, BAR_X + fill_w, BAR_Y + BAR_H),
                                radius=r, fill=bar_color(pct))

    # Now / End time labels
    draw.text((BAR_X, 14), 'Now: ' + cur,
              font=fonts['small'], fill=C_TEXT_DIM)
    end_str = 'End: ' + end
    ew = int(draw.textlength(end_str, font=fonts['small']))
    draw.text((W - BAR_X - ew, 14), end_str,
              font=fonts['small'], fill=C_TEXT_DIM)

    # Large centred message
    draw.text((W // 2, 110), message,
              font=fonts['big'], fill=C_TEXT_HI, anchor='mm')

    # Countdown label
    mins, secs = divmod(rem, 60)
    cstr = '{:02d}:{:02d} remaining'.format(mins, secs)
    draw.text((W // 2, 210), cstr,
              font=fonts['medium'], fill=C_TEXT_SOFT, anchor='mm')

    return to_sixel(img)


# ── Terminal geometry ─────────────────────────────────────────────────────────
def _cell_size_px() -> tuple[int, int]:
    """Query terminal for cell size in pixels via XTWINOPS CSI 16 t.
    Falls back to (8, 16) if the terminal doesn't respond."""
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            sys.stdout.write('\033[16t')
            sys.stdout.flush()
            buf = b''
            while select.select([fd], [], [], 0.15)[0]:
                buf += os.read(fd, 64)
            m = re.search(rb'\x1b\[6;(\d+);(\d+)t', buf)
            if m:
                return int(m.group(2)), int(m.group(1))   # (cell_w, cell_h)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    except Exception:
        pass
    return 8, 16


def _centered_home(img_w: int, img_h: int) -> str:
    """Return an ANSI cursor-position escape that centers the image."""
    cell_w, cell_h = _cell_size_px()
    try:
        ts = os.get_terminal_size()
        img_cols = (img_w + cell_w - 1) // cell_w
        img_rows = (img_h + cell_h - 1) // cell_h
        col = max(1, (ts.columns - img_cols) // 2 + 1)
        row = max(1, (ts.lines   - img_rows) // 2 + 1)
    except OSError:
        col, row = 1, 1
    return '\033[{};{}H'.format(row, col)


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: {} <seconds|hh:mm> <message>'.format(sys.argv[0]), file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1]
    message = sys.argv[2]
    now = time.time()

    if re.fullmatch(r'\d{1,2}:\d{2}', arg):
        # End time given as hh:mm – resolve to today, or tomorrow if already past
        hh, mm = int(arg.split(':')[0]), int(arg.split(':')[1])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            print('Error: time must be hh:mm with 00:00–23:59', file=sys.stderr)
            sys.exit(1)
        today = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        end_epoch = today.timestamp()
        if end_epoch <= now:
            end_epoch += 86400   # roll to tomorrow
    else:
        try:
            duration = int(arg)
            if duration <= 0:
                raise ValueError
        except ValueError:
            print('Error: first argument must be a positive integer (seconds) '
                  'or a time in hh:mm format', file=sys.stderr)
            sys.exit(1)
        end_epoch = now + duration

    if end_epoch - now < 1:
        print('Error: end time is in the past', file=sys.stderr)
        sys.exit(1)

    fonts    = load_fonts()
    img      = Image.new('RGB', (W, H), C_BG)
    draw     = ImageDraw.Draw(img)
    end_str  = datetime.fromtimestamp(end_epoch).strftime('%H:%M:%S')
    duration = end_epoch - now   # total seconds, used for bar fill ratio
    home     = _centered_home(W, H)

    # Raw mode: keypresses arrive immediately without waiting for Enter
    fd       = sys.stdin.fileno()
    old_term = termios.tcgetattr(fd)
    tty.setraw(fd)

    resized = [False]

    def _on_resize(sig, frame):
        resized[0] = True

    signal.signal(signal.SIGWINCH, _on_resize)

    sys.stdout.write('\033[?25l\033[2J' + home)
    sys.stdout.flush()

    try:
        while True:
            if resized[0]:
                resized[0] = False
                home = _centered_home(W, H)
                sys.stdout.write('\033[2J')
                sys.stdout.flush()

            rem = max(0, int(end_epoch - time.time()))
            cur = datetime.now().strftime('%H:%M:%S')

            sys.stdout.write(home)
            sys.stdout.write(draw_frame(img, draw, fonts, rem, cur, end_str,
                                        duration, message))
            sys.stdout.flush()

            if rem == 0:
                break

            # Sleep until the next tick; wake early on q / ESC / Ctrl-C
            deadline = end_epoch - (rem - 1)
            while time.time() < deadline:
                left = deadline - time.time()
                if select.select([sys.stdin], [], [], max(0.0, left))[0]:
                    ch = os.read(fd, 16)
                    if b'q' in ch or b'Q' in ch or b'\x1b' in ch or b'\x03' in ch:
                        raise SystemExit(0)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        sys.stdout.write('\033[?25h\r\n')
        sys.stdout.flush()

    print('\033[1mTimer complete!\033[0m')


if __name__ == '__main__':
    main()
