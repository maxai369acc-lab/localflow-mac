"""Tray icon (pystray) + floating pill overlay (tkinter).

Design language — "one ribbon, three behaviors":
  listening   mirrored amplitude bars in the indigo→violet gradient
  processing  a traveling sine ribbon (thinking, not frozen)
  success     the ribbon flattens and draws a checkmark, then fades out
  error       red is reserved for real failures: flat red line + reason

The tkinter root runs in the MAIN thread; every other thread talks to it
through a queue. The tray icon runs on its own thread (fine on Windows).
"""

from __future__ import annotations

import math
import queue
import sys
import threading
from collections import deque

try:
    import tkinter as tk
    HAS_TK = True
except Exception:  # tkinter missing from this interpreter
    HAS_TK = False

import pystray
from PIL import Image, ImageDraw

from .config import DATA_DIR, open_path
from .widget import FlowBar

# ---- palette ("Ink" theme) --------------------------------------------------
INK = "#101319"       # pill body / window ground
INK_EDGE = "#2A3040"  # subtle pill border
MIST = "#E9EDF4"      # primary text
DIM = "#8B93A5"       # secondary text
FLOW = "#7C8CF8"      # voice accent (listening)
PULSE = "#B48CF2"     # thinking accent (processing)
MINT = "#4ADE9E"      # inserted
ALERT = "#F2555A"     # errors only
KEY = "#010203"       # transparentcolor key (never drawn otherwise)

PILL_W, PILL_H = 340, 54
TICK_MS = 40  # 25 fps


def _lerp_color(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a, b = int(c1[1:], 16), int(c2[1:], 16)
    r = round(((a >> 16) & 255) + (((b >> 16) & 255) - ((a >> 16) & 255)) * t)
    g = round(((a >> 8) & 255) + (((b >> 8) & 255) - ((a >> 8) & 255)) * t)
    bl = round((a & 255) + ((b & 255) - (a & 255)) * t)
    return f"#{r:02x}{g:02x}{bl:02x}"


# ---- tray icon artwork -------------------------------------------------------

def make_icon(state: str) -> Image.Image:
    """Waveform mark on an ink squircle, tinted per state (drawn 4x, downsampled)."""
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((10, 10, S - 10, S - 10), radius=58, fill=INK)
    heights = (0.34, 0.66, 0.98, 0.58, 0.30)
    n, bw, gap = len(heights), 26, 15
    x = (S - (n * bw + (n - 1) * gap)) / 2
    cy = S / 2
    for i, hf in enumerate(heights):
        t = i / (n - 1)
        if state == "recording":
            col = _lerp_color(FLOW, PULSE, t)
        elif state == "processing":
            col = _lerp_color(PULSE, FLOW, t)
        elif state == "error":
            col = ALERT
        elif state == "starting":
            col = "#5A6172"
        else:  # idle — the gradient, dimmed toward ink
            col = _lerp_color(_lerp_color(FLOW, PULSE, t), INK, 0.45)
        h = hf * 158
        d.rounded_rectangle((x, cy - h / 2, x + bw, cy + h / 2),
                            radius=bw / 2, fill=col)
        x += bw + gap
    return img.resize((64, 64), Image.LANCZOS)


class Ui:
    """Owns the tk root + pill overlay. Construct, then run_forever() on main thread."""

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._levels = deque(maxlen=24)
        self._mode = "hidden"   # hidden | listening | processing | success | error
        self._tick = 0
        self._mode_t0 = 0       # tick when current mode began
        self._handsfree = False
        self._command = False
        self._words = 0
        self._err_msg = ""
        self._fade: float | None = None   # current alpha while fading out
        self._fade_step = 0.11
        self.root = None
        self._overlay = None
        self._canvas = None
        self._on_close = None
        # floating flow bar (glass lozenge; see widget.py)
        self.on_widget_click = None       # set by App
        self.on_widget_move = None        # set by App; receives (x, y)
        self._flowbar: FlowBar | None = None
        self._widget_state = "starting"
        self._widget_pos: tuple[int, int] | None = None

    # ---- thread-safe API -------------------------------------------------
    def show_listening(self, text: str = "Listening…", kind: str = "dictation") -> None:
        self._q.put(("listen", ("hands-free" in text, kind == "command")))

    def show_processing(self, text: str = "Processing…") -> None:
        self._q.put(("process", None))

    def show_success(self, words: int) -> None:
        self._q.put(("success", words))

    def show_error(self, msg: str) -> None:
        self._q.put(("error", msg))

    def hide_overlay(self) -> None:
        self._q.put(("hide", None))

    def set_level(self, rms: float) -> None:
        # push directly; deque is thread-safe for append
        self._levels.append(min(1.0, rms * 14))

    def call(self, fn) -> None:
        """Run fn() on the tk thread (for building editor windows)."""
        self._q.put(("call", fn))

    def configure_widget(self, show: bool, pos: tuple[int, int] | None = None) -> None:
        self._q.put(("widget", (show, pos)))

    def widget_state(self, state: str) -> None:
        self._q.put(("widget_state", state))

    def quit(self) -> None:
        self._q.put(("quit", None))

    # ---- main-thread loop --------------------------------------------------
    def run_forever(self, on_close=None) -> None:
        if not HAS_TK:
            # degraded mode: still service the queue so quit() works
            while True:
                op, arg = self._q.get()
                if op == "quit":
                    return
                if op == "call":
                    try:
                        arg()
                    except Exception:
                        pass
            return
        self._on_close = on_close
        self.root = tk.Tk()
        self.root.withdraw()
        self._build_overlay()
        self.root.after(TICK_MS, self._poll)
        self.root.mainloop()

    def _build_overlay(self) -> None:
        ov = tk.Toplevel(self.root)
        ov.withdraw()
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        bg = INK
        try:
            ov.attributes("-transparentcolor", KEY)
            bg = KEY  # rounded pill: everything KEY-colored is see-through
        except tk.TclError:
            pass      # fall back to a square pill
        sw, sh = ov.winfo_screenwidth(), ov.winfo_screenheight()
        ov.geometry(f"{PILL_W}x{PILL_H}+{(sw - PILL_W) // 2}+{sh - PILL_H - 90}")
        ov.configure(bg=bg)
        self._canvas = tk.Canvas(ov, width=PILL_W, height=PILL_H, bg=bg,
                                 highlightthickness=0, bd=0)
        self._canvas.pack()
        self._paint_pill_bg()
        self._overlay = ov

    def _paint_pill_bg(self) -> None:
        """Static rounded-pill body (radius = height/2), tagged 'bg'."""
        c, w, h = self._canvas, PILL_W, PILL_H
        c.create_oval(1, 1, h - 1, h - 1, fill=INK, outline=INK_EDGE, tags="bg")
        c.create_oval(w - h + 1, 1, w - 1, h - 1, fill=INK, outline=INK_EDGE,
                      tags="bg")
        c.create_rectangle(h // 2, 1, w - h // 2, h - 1, fill=INK, outline=INK,
                           tags="bg")
        c.create_line(h // 2, 1, w - h // 2, 1, fill=INK_EDGE, tags="bg")
        c.create_line(h // 2, h - 1, w - h // 2, h - 1, fill=INK_EDGE, tags="bg")

    # ---- state transitions (tk thread) --------------------------------------
    def _enter(self, mode: str) -> None:
        self._mode = mode
        self._mode_t0 = self._tick
        self._fade = None
        try:
            self._overlay.attributes("-alpha", 0.97)
        except tk.TclError:
            pass
        self._overlay.deiconify()
        self._overlay.attributes("-topmost", True)

    def _start_fade(self, fast: bool = False) -> None:
        if self._fade is None:
            self._fade = 0.97
        self._fade_step = 0.28 if fast else 0.11

    def _poll(self) -> None:
        try:
            while True:
                op, arg = self._q.get_nowait()
                if op == "listen":
                    self._handsfree, self._command = arg
                    self._levels.clear()
                    if not self._bar_covers():
                        self._enter("listening")
                elif op == "process":
                    if not self._bar_covers():
                        self._enter("processing")
                elif op == "success":
                    self._words = int(arg)
                    if self._bar_covers():
                        self._flowbar.note_words(self._words)
                    else:
                        self._enter("success")
                elif op == "error":
                    self._err_msg = str(arg)[:34]
                    if self._bar_covers():
                        self._flowbar.note_error(self._err_msg)
                    else:
                        self._enter("error")
                elif op == "hide":
                    # success/error flashes own their exit; don't cut them short
                    if self._mode in ("listening", "processing"):
                        self._start_fade(fast=True)
                elif op == "widget":
                    show, pos = arg
                    if pos:
                        self._widget_pos = tuple(pos)
                    if show:
                        self._show_widget()
                    elif self._flowbar is not None:
                        self._flowbar.hide()
                elif op == "widget_state":
                    if arg != self._widget_state:
                        self._widget_state = arg
                        if self._flowbar is not None:
                            self._flowbar.set_state(arg)
                elif op == "call":
                    try:
                        arg()
                    except Exception:
                        pass
                elif op == "quit":
                    self.root.quit()
                    return
        except queue.Empty:
            pass

        if self._mode != "hidden":
            age = self._tick - self._mode_t0
            if self._mode == "success" and age > 32:   # ~1.3 s
                self._start_fade()
            elif self._mode == "error" and age > 45:   # ~1.8 s
                self._start_fade()
            if self._fade is not None:
                self._fade -= self._fade_step
                if self._fade <= 0:
                    self._fade = None
                    self._mode = "hidden"
                    self._overlay.withdraw()
                else:
                    try:
                        self._overlay.attributes("-alpha", self._fade)
                    except tk.TclError:  # no alpha support: hide immediately
                        self._fade = None
                        self._mode = "hidden"
                        self._overlay.withdraw()
            if self._mode != "hidden":
                self._draw()
        self._tick += 1
        self.root.after(TICK_MS, self._poll)

    # ---- drawing -------------------------------------------------------------
    def _draw(self) -> None:
        c, h = self._canvas, PILL_H
        c.delete("dyn")
        mid = h / 2
        x0, x1 = 52, PILL_W - 70          # ribbon region
        age = self._tick - self._mode_t0

        dot = {"listening": PULSE if self._command else FLOW,
               "processing": PULSE,
               "success": MINT, "error": ALERT}[self._mode]
        c.create_oval(20, mid - 7, 34, mid + 7, fill=_lerp_color(INK, dot, 0.3),
                      outline="", tags="dyn")
        c.create_oval(23, mid - 4, 31, mid + 4, fill=dot, outline="", tags="dyn")

        meta = ""
        if self._mode == "listening":
            secs = int(age * TICK_MS / 1000)
            meta = f"{secs // 60}:{secs % 60:02d}"
            if self._command:
                meta = "cmd " + meta
            elif self._handsfree:
                meta = "∞ " + meta
            self._draw_bars(c, x0, x1, mid)
        elif self._mode == "processing":
            meta = "…"
            self._draw_ribbon(c, x0, x1, mid)
        elif self._mode == "success":
            meta = f"{self._words} wds"
            self._draw_check(c, x0, x1, mid, min(1.0, age / 9.0))
        elif self._mode == "error":
            meta = "!"
            shake = math.sin(self._tick * 1.4) * 3 if age < 10 else 0
            c.create_line(x0 + shake, mid + 12, x1 + shake, mid + 12,
                          fill=ALERT, width=3, capstyle="round", tags="dyn")
            c.create_text((x0 + x1) / 2, mid - 4, text=self._err_msg,
                          fill="#FFB3B6", font=("Segoe UI", 9), tags="dyn")

        c.create_text(PILL_W - 24, mid, text=meta, anchor="e", fill=DIM,
                      font=("Consolas", 9), tags="dyn")

    def _draw_bars(self, c, x0, x1, mid) -> None:
        n = 24
        step = (x1 - x0) / n
        levels = list(self._levels)
        max_h = PILL_H - 18
        c1, c2 = (PULSE, FLOW) if self._command else (FLOW, PULSE)
        for i in range(n):
            lv = levels[-(n - i)] if len(levels) >= (n - i) else 0.04
            bh = max(4.0, min(max_h, lv * max_h * 2.6))
            x = x0 + i * step + step / 2
            c.create_line(x, mid - bh / 2, x, mid + bh / 2,
                          fill=_lerp_color(c1, c2, i / (n - 1)),
                          width=5, capstyle="round", tags="dyn")

    def _draw_ribbon(self, c, x0, x1, mid) -> None:
        span = x1 - x0
        amp = (PILL_H - 22) / 2
        prev = None
        for x in range(x0, x1 + 1, 7):
            u = (x - x0) / span
            taper = math.sin(u * math.pi)
            y = mid + math.sin(u * math.pi * 3 - self._tick * 0.35) * amp * taper
            if prev is not None:
                c.create_line(prev[0], prev[1], x, y,
                              fill=_lerp_color(PULSE, FLOW, u),
                              width=4, capstyle="round", tags="dyn")
            prev = (x, y)

    def _draw_check(self, c, x0, x1, mid, p) -> None:
        cx, k = (x0 + x1) / 2, 9
        c.create_line(x0, mid, cx - k - 8, mid, fill=MINT, width=4,
                      capstyle="round", tags="dyn")
        c.create_line(cx + k * 1.4 + 8, mid, x1, mid, fill=MINT, width=4,
                      capstyle="round", tags="dyn")
        pts = [(cx - k, mid)]
        if p < 0.45:
            q = p / 0.45
            pts.append((cx - k + q * k * 0.7, mid + q * k * 0.8))
        else:
            pts.append((cx - k * 0.3, mid + k * 0.8))
            q = (p - 0.45) / 0.55
            pts.append((cx - k * 0.3 + q * k * 1.7, mid + k * 0.8 - q * k * 1.7))
        for a, b in zip(pts, pts[1:]):
            c.create_line(a[0], a[1], b[0], b[1], fill=MINT, width=4,
                          capstyle="round", joinstyle="round", tags="dyn")


    # ---- floating flow bar (glass lozenge; see widget.py) ----------------------

    def _bar_covers(self) -> bool:
        """The visible flow bar carries all states; the pill stays out of it."""
        return self._flowbar is not None and self._flowbar.visible

    def _show_widget(self) -> None:
        if self._flowbar is None:
            self._flowbar = FlowBar(self.root, self._levels)
            self._flowbar.on_action = self._widget_clicked
            self._flowbar.on_move = self._widget_moved
            self._flowbar.set_state(self._widget_state)
        self._flowbar.show(self._widget_pos)

    def _widget_clicked(self, action: str) -> None:
        if self.on_widget_click:
            try:
                self.on_widget_click(action)
            except Exception:
                pass

    def _widget_moved(self, x: int, y: int) -> None:
        self._widget_pos = (x, y)
        if self.on_widget_move:
            try:
                self.on_widget_move(x, y)
            except Exception:
                pass


class Tray:
    """System tray icon + menu. Runs pystray on a background thread."""

    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon("localflow", make_icon("starting"),
                                 "Local Flow — starting")
        self.icon.menu = self._menu()
        self._thread: threading.Thread | None = None

    def _menu(self) -> pystray.Menu:
        a = self.app
        M, I = pystray.Menu, pystray.MenuItem

        def level_item(name: str):
            return I(name.capitalize(),
                     lambda *_, n=name: a.set_cleanup_level(n),
                     radio=True,
                     checked=lambda item, n=name: a.cfg["cleanup_level"] == n)

        return M(
            I(lambda item: f"Local Flow — {a.status_text}", None, enabled=False),
            M.SEPARATOR,
            I("Open Local Flow…", lambda *_: a.open_main(), default=True),
            I("Hands-free dictation", lambda *_: a.on_handsfree_toggle(),
              checked=lambda item: a.handsfree_active),
            I("Paste last transcript", lambda *_: a.on_paste_last()),
            I("Copy last transcript", lambda *_: a.on_copy_last()),
            M.SEPARATOR,
            I("Cleanup level", M(*[level_item(n) for n in
                                   ("off", "light", "medium", "high")])),
            I("Mute other apps' sound while dictating",
              lambda *_: a.set_audio_option(
                  "mute_during_dictation",
                  not a.cfg["audio"].get("mute_during_dictation", True)),
              checked=lambda item: a.cfg["audio"].get(
                  "mute_during_dictation", True)),
            I("Developer mode", M(
                I(lambda item: (f"{len(a.dev_identifiers)} identifiers from "
                                f"{len(a.cfg['dev'].get('workspace_folders', []))} folder(s)"),
                  None, enabled=False),
                I("Add workspace folder…", lambda *_: a.add_workspace_folder()),
                I("Rescan identifiers", lambda *_: a.rescan_dev()),
            )),
            I("History…", lambda *_: a.open_history()),
            I("Dictionary…", lambda *_: a.open_dictionary()),
            I("Snippets…", lambda *_: a.open_snippets()),
            I("Open data folder", lambda *_: open_path(DATA_DIR)),
            I("Start with Windows", lambda *_: a.toggle_autostart(),
              checked=lambda item: a.autostart_enabled),
            M.SEPARATOR,
            I(lambda item: ("Install update"
                            if a.update_available is None
                            else f"Install update v{a.update_available.version}"),
              lambda *_: a.install_update(),
              visible=lambda item: a.update_available is not None),
            I("Quit Local Flow", lambda *_: a.quit()),
        )

    def start(self) -> None:
        if sys.platform == "darwin":
            # AppKit status items must own the main thread, which tk holds;
            # the floating bar + main window cover tray duties on macOS.
            return
        self._thread = threading.Thread(target=self.icon.run, daemon=True,
                                        name="tray")
        self._thread.start()

    def set_state(self, state: str) -> None:
        try:
            self.app.ui.widget_state(state)
        except Exception:
            pass
        try:
            self.icon.icon = make_icon(state)
            self.icon.title = f"Local Flow — {state}"
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, title: str, msg: str) -> None:
        try:
            self.icon.notify(msg[:250], title[:60])
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
