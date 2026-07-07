"""Local Flow main window — Ink & Signal theme.

Dark sidebar + raised content panel. Tabs: Home, Insights, Dictionary,
Snippets, Style, Settings. Must be built on the tk main thread.
"""

from __future__ import annotations

import getpass
import tkinter as tk

from . import __version__
from .config import DATA_DIR, open_path, save_config
from .editors import build_dictionary, build_snippets
from .theme import (ALERT, CARD, CARD2, DIMT, FLOW, FONT, GROUND, LINE, MINT,
                    NAV_ACTIVE, PULSE, TEXT, btn_ghost, btn_primary,
                    display_font, ensure_style, eyebrow, icon_font,
                    mono_font, signal_color, signal_strip, tabs_row)

NAV = [("home", "Home", ""),
       ("insights", "Insights", ""),
       ("dictionary", "Dictionary", ""),
       ("snippets", "Snippets", ""),
       ("style", "Style", "")]
BOTTOM_NAV = [("settings", "Settings", "")]

STYLE_CARDS = [
    ("formal", "Formal.", "Caps + Punctuation",
     "Hey, are you free for lunch tomorrow? Noon works for me."),
    ("casual", "Casual", "Caps + Less punctuation",
     "Hey are you free for lunch tomorrow? Noon works for me"),
    ("very_casual", "very casual", "No Caps + Less punctuation",
     "hey are you free for lunch tomorrow? noon works for me"),
]
STYLE_TABS = [("Personal messages", "chat"), ("Work messages", "other"),
              ("Email", "email"), ("Docs", "doc"), ("Code", "code")]

STATE_LAMP = {"idle": MINT, "recording": FLOW, "processing": PULSE,
              "error": ALERT, "starting": DIMT}


class MainWindow:
    _instance: "MainWindow | None" = None

    @classmethod
    def open(cls, app, tab: str = "home") -> None:
        if tab == "recent":
            tab = "home"
        inst = cls._instance
        if inst is not None and inst.alive():
            inst.show(tab)
            return
        cls._instance = cls(app, tab)

    def __init__(self, app, tab: str = "home"):
        self.app = app
        self.win = tk.Toplevel(app.ui.root)
        self.win.title("Local Flow")
        self.win.geometry("1040x640")
        self.win.minsize(880, 540)
        self.win.configure(bg=GROUND)
        ensure_style(self.win)
        self._icons = icon_font(self.win)
        self._disp = display_font(self.win)
        self._mono = mono_font(self.win)

        self._nav_btns: dict[str, tuple[tk.Frame, tk.Canvas]] = {}
        self._frames: dict[str, tk.Frame] = {}
        self._current = ""

        self._build_sidebar()
        # raised content panel inset on the ink ground
        holder = tk.Frame(self.win, bg=GROUND)
        holder.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(holder, bg=CARD, highlightthickness=1,
                                highlightbackground=LINE)
        self.content.pack(fill="both", expand=True, padx=(0, 14), pady=14)
        self.show(tab)
        self.win.attributes("-topmost", True)
        self.win.after(200, lambda: self.win.attributes("-topmost", False))

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    # ---- sidebar --------------------------------------------------------------
    def _nav_item(self, side, key, text, glyph) -> None:
        row = tk.Frame(side, bg=GROUND, cursor="hand2")
        row.pack(fill="x", padx=10, pady=1)
        # active mark: a sliver of the signal gradient at the left edge
        mark = tk.Canvas(row, width=3, height=30, bg=GROUND,
                         highlightthickness=0)
        mark.pack(side="left")
        inner = tk.Frame(row, bg=GROUND)
        inner.pack(fill="x")
        if self._icons:
            tk.Label(inner, text=glyph, font=(self._icons, 11), bg=GROUND,
                     fg=TEXT, width=2).pack(side="left", padx=(6, 6), pady=6)
        tk.Label(inner, text=text, bg=GROUND, fg=DIMT,
                 font=(FONT, 10)).pack(side="left", padx=(8, 0), pady=6)
        for w in (row, inner, *inner.winfo_children()):
            w.bind("<Button-1>", lambda e, k=key: self.show(k))
        self._nav_btns[key] = (row, mark)

    def _build_sidebar(self) -> None:
        side = tk.Frame(self.win, bg=GROUND, width=200)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        head = tk.Frame(side, bg=GROUND)
        head.pack(fill="x", pady=(20, 18), padx=18)
        logo = tk.Canvas(head, width=22, height=22, bg=GROUND,
                         highlightthickness=0)
        logo.pack(side="left")
        for i, h in enumerate((8, 15, 20, 12, 7)):
            x = 3 + i * 4.2
            logo.create_line(x, 11 - h / 2, x, 11 + h / 2,
                             fill=signal_color(i / 4), width=2.6,
                             capstyle="round")
        tk.Label(head, text="Local Flow", bg=GROUND, fg=TEXT,
                 font=(self._disp, 13, "bold")).pack(side="left", padx=8)

        for key, text, glyph in NAV:
            self._nav_item(side, key, text, glyph)

        bottom = tk.Frame(side, bg=GROUND)
        bottom.pack(side="bottom", fill="x", pady=10)
        tk.Frame(bottom, bg=LINE, height=1).pack(fill="x", padx=14,
                                                 pady=(0, 8))
        for key, text, glyph in BOTTOM_NAV:
            self._nav_item(bottom, key, text, glyph)
        # status lamp: state dot + engine readout, like hardware
        lamp_row = tk.Frame(bottom, bg=GROUND)
        lamp_row.pack(fill="x", padx=18, pady=(6, 4))
        self.lamp = tk.Canvas(lamp_row, width=8, height=8, bg=GROUND,
                              highlightthickness=0)
        self.lamp.pack(side="left", pady=2)
        self.status = tk.Label(lamp_row, text="", bg=GROUND, fg=DIMT,
                               font=(self._mono, 8), justify="left",
                               anchor="w")
        self.status.pack(side="left", fill="x", padx=(6, 0))
        self._tick()

    def _tick(self) -> None:
        if not self.alive():
            return
        a = self.app
        eng = a.asr.name if a.asr else "loading…"
        self.status.config(
            text=f"{eng}\n{a.state} · llm {'✓' if a.llm_ready else '–'}")
        self.lamp.delete("all")
        self.lamp.create_oval(1, 1, 7, 7,
                              fill=STATE_LAMP.get(a.state, DIMT), width=0)
        self.win.after(900, self._tick)

    # ---- navigation ---------------------------------------------------------------
    def show(self, tab: str) -> None:
        keys = [k for k, *_ in NAV + BOTTOM_NAV]
        if tab not in keys:
            tab = "home"
        for key, (row, mark) in self._nav_btns.items():
            active = key == tab
            bg = NAV_ACTIVE if active else GROUND
            row.config(bg=bg)
            mark.config(bg=bg)
            mark.delete("all")
            if active:
                for y in range(6, 24):
                    mark.create_line(0, y, 3, y,
                                     fill=signal_color((y - 6) / 18))
            for w in row.winfo_children():
                if isinstance(w, tk.Frame):
                    w.config(bg=bg)
                    for w2 in w.winfo_children():
                        w2.config(bg=bg,
                                  fg=TEXT if active else DIMT)
        if self._current:
            self._frames[self._current].pack_forget()
        if tab not in self._frames:
            f = tk.Frame(self.content, bg=CARD)
            {"home": self._build_home,
             "insights": self._build_insights,
             "dictionary": self._build_dictionary,
             "snippets": self._build_snippets,
             "style": self._build_style,
             "settings": self._build_settings}[tab](f)
            self._frames[tab] = f
        self._frames[tab].pack(fill="both", expand=True)
        self._current = tab
        if tab == "home":
            self._refresh_home()
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def _header(self, f, title: str, button: tuple[str, object] | None = None,
                badge: str = "") -> None:
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x", padx=28, pady=(24, 4))
        tk.Label(row, text=title, bg=CARD, fg=TEXT,
                 font=(self._disp, 14, "bold")).pack(side="left")
        if badge:
            tk.Label(row, text=badge, bg=CARD2, fg=PULSE,
                     font=(FONT, 7, "bold"), padx=6,
                     pady=2).pack(side="left", padx=8)
        if button:
            btn_primary(row, button[0], button[1]).pack(side="right")

    # ---- Home ----------------------------------------------------------------------
    def _build_home(self, f) -> None:
        user = getpass.getuser()
        self._header(f, f"Welcome back, {user}")

        body = tk.Frame(f, bg=CARD)
        body.pack(fill="both", expand=True, padx=28, pady=(6, 16))
        left = tk.Frame(body, bg=CARD)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=CARD, width=230)
        right.pack(side="left", fill="y", padx=(18, 0))
        right.pack_propagate(False)

        signal_strip(left, "Dictate in any app on this PC.",
                     "Everything runs locally — your voice never leaves "
                     "this machine.",
                     kbd="hold  Ctrl + Win  and speak",
                     state_fn=lambda: self.app.state).pack(fill="x")

        row = tk.Frame(left, bg=CARD)
        row.pack(fill="x", pady=(18, 4))
        eyebrow(row, "Today").pack(side="left")
        tk.Label(row, text="hit Copy to reuse a take", bg=CARD, fg=DIMT,
                 font=(FONT, 8)).pack(side="left", padx=(10, 0), pady=(2, 0))
        btn_ghost(row, "Refresh", self._refresh_home).pack(side="right")

        self.today = tk.Frame(left, bg=CARD, highlightthickness=1,
                              highlightbackground=LINE)
        self.today.pack(fill="both", expand=True)

        # right column meter cards
        self.stat_card = tk.Frame(right, bg=CARD2)
        self.stat_card.pack(fill="x")
        self.engine_card = tk.Frame(right, bg=CARD2)
        self.engine_card.pack(fill="x", pady=(12, 0))

    def _stat(self, parent, big: str, small: str) -> None:
        row = tk.Frame(parent, bg=CARD2)
        row.pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(row, text=big, bg=CARD2, fg=TEXT,
                 font=(self._mono, 15)).pack(side="left")
        tk.Label(row, text=" " + small, bg=CARD2, fg=DIMT,
                 font=(FONT, 9)).pack(side="left", pady=(6, 0))

    def _refresh_home(self) -> None:
        if not hasattr(self, "today"):
            return
        for w in self.today.winfo_children():
            w.destroy()
        rows = self.app.db.history_last(40)
        shown = 0
        for (hid, ts, app, mode, raw, final, *_rest) in rows:
            text = (final or raw or "").replace("\n", " ")
            if not text:
                continue
            r = tk.Frame(self.today, bg=CARD)
            r.pack(fill="x")
            t = ts[11:16] if len(ts) >= 16 else ts
            tk.Label(r, text=t, bg=CARD, fg=DIMT, width=8, anchor="w",
                     font=(self._mono, 8)).pack(side="left", padx=(14, 4),
                                                pady=7)
            full = final or raw or ""
            btn = tk.Button(r, text="Copy", relief="flat", bd=0,
                            cursor="hand2", padx=8, pady=1, font=(FONT, 8),
                            bg=CARD, fg=DIMT, activebackground=LINE,
                            activeforeground=TEXT)
            btn.pack(side="right", padx=(6, 12))
            lbl = tk.Label(r, text=text[:95], bg=CARD, fg=TEXT, anchor="w",
                           font=(FONT, 10))
            lbl.pack(side="left", fill="x", expand=True, pady=7)
            tk.Frame(self.today, bg=LINE, height=1).pack(fill="x")

            def _copy(_e=None, txt=full, b=btn):
                try:
                    from .insert import set_clipboard_text
                    set_clipboard_text(txt)
                except Exception:
                    self.win.clipboard_clear()
                    self.win.clipboard_append(txt)
                b.config(text="Copied ✓", fg=MINT)

                def _revert(b=b):
                    try:
                        b.config(text="Copy", fg=DIMT)
                    except tk.TclError:
                        pass
                self.win.after(1400, _revert)
            btn.config(command=_copy)
            for w in (r, lbl):
                w.bind("<Double-1>", _copy)
            shown += 1
            if shown >= 9:
                break
        if not shown:
            tk.Label(self.today, text="No dictations yet today — hold "
                     "Ctrl+Win and say something.", bg=CARD, fg=DIMT,
                     font=(FONT, 10)).pack(pady=30)

        for card in (self.stat_card, self.engine_card):
            for w in card.winfo_children():
                w.destroy()
        s = self.app.db.stats()
        kw = s["total_words"]
        big = f"{kw/1000:.1f}K" if kw >= 1000 else str(kw)
        self._stat(self.stat_card, big, "total words")
        self._stat(self.stat_card, str(s["dictations"]), "dictations")
        self._stat(self.stat_card, f"{s['streak']} day", "streak")
        tk.Frame(self.stat_card, bg=CARD2, height=12).pack()

        a = self.app
        tk.Label(self.engine_card, text="Your engine", bg=CARD2, fg=TEXT,
                 font=(FONT, 10, "bold")).pack(anchor="w", padx=16,
                                               pady=(12, 2))
        eng = a.asr.name if a.asr else "loading…"
        tk.Label(self.engine_card, text=eng, bg=CARD2, fg=DIMT,
                 font=(self._mono, 8)).pack(anchor="w", padx=16)
        btn_ghost(self.engine_card, "Switch in Settings",
                  lambda: self.show("settings")).pack(anchor="w", padx=16,
                                                      pady=(8, 14))

    # ---- Insights -------------------------------------------------------------------
    def _build_insights(self, f) -> None:
        self._header(f, "Insights")
        strip, select = tabs_row(f, ["Your Usage"], lambda n: None)
        strip.pack(fill="x", padx=28, pady=(2, 0))
        select("Your Usage")
        tk.Frame(f, bg=LINE, height=1).pack(fill="x", padx=28, pady=(0, 14))

        s = self.app.db.stats()
        cards = tk.Frame(f, bg=CARD)
        cards.pack(fill="x", padx=28)

        def stat_card(big, small, extra=""):
            c = tk.Frame(cards, bg=CARD2)
            c.pack(side="left", fill="both", expand=True, padx=(0, 12))
            tk.Label(c, text=big, bg=CARD2, fg=TEXT,
                     font=(self._mono, 20)).pack(anchor="w", padx=18,
                                                 pady=(16, 0))
            eyebrow(c, small, bg=CARD2).pack(anchor="w", padx=18, pady=(2, 4))
            if extra:
                tk.Label(c, text=extra, bg=CARD2, fg=DIMT,
                         font=(FONT, 9)).pack(anchor="w", padx=18,
                                              pady=(0, 14))
            else:
                tk.Frame(c, bg=CARD2, height=14).pack()
            return c

        stat_card(f"{s['total_words']:,}", "total words dictated")
        stat_card(str(s["polished"]), "cleanups made by flow",
                  "LLM-polished dictations")
        stat_card(f"{s['streak']} day", "current streak")

        usage = tk.Frame(f, bg=CARD2)
        usage.pack(fill="both", expand=True, padx=28, pady=16)
        tk.Label(usage, text="Desktop usage", bg=CARD2, fg=TEXT,
                 font=(self._disp, 13, "bold")).pack(anchor="w", padx=18,
                                                     pady=(14, 8))
        total = sum(n for _, n in s["apps"]) or 1
        for app_name, n in s["apps"]:
            row = tk.Frame(usage, bg=CARD2)
            row.pack(fill="x", padx=18, pady=4)
            pct = int(round(100 * n / total))
            bar = tk.Canvas(row, height=22, width=320, bg=CARD2,
                            highlightthickness=0)
            bar.pack(side="left")
            w = max(34, int(320 * n / total))
            for x in range(0, w, 4):
                bar.create_rectangle(x, 0, min(x + 4, w), 22,
                                     fill=signal_color(x / 320, 0.25),
                                     width=0)
            bar.create_text(8, 11, text=f"{pct}%", anchor="w",
                            fill=GROUND, font=(FONT, 8, "bold"))
            tk.Label(row, text=f"{n} in {app_name.replace('.exe', '').upper()}",
                     bg=CARD2, fg=TEXT, font=(FONT, 9)).pack(side="left",
                                                             padx=10)
        if not s["apps"]:
            tk.Label(usage, text="Dictate a bit first — app usage shows up "
                     "here.", bg=CARD2, fg=DIMT,
                     font=(FONT, 10)).pack(padx=18, pady=10, anchor="w")

    # ---- Dictionary / Snippets ---------------------------------------------------------
    def _build_dictionary(self, f) -> None:
        self._header(f, "Dictionary")
        signal_strip(f, "Local Flow spells it your way.",
                     "Teach it names and jargon — add what it should write, "
                     "and what your voice comes out as.", height=90
                     ).pack(fill="x", padx=28, pady=(8, 0))
        build_dictionary(f, self.app.db)

    def _build_snippets(self, f) -> None:
        self._header(f, "Snippets")
        signal_strip(f, "Stop re-typing the same things.",
                     "Save text you use often, then just say the trigger "
                     "word to drop it in.", height=90
                     ).pack(fill="x", padx=28, pady=(8, 0))
        build_snippets(f, self.app.db)

    # ---- Style -----------------------------------------------------------------------
    def _build_style(self, f) -> None:
        self._header(f, "Style")
        self._style_cat = "chat"
        strip, select = tabs_row(f, [n for n, _ in STYLE_TABS],
                                 self._style_tab_selected)
        strip.pack(fill="x", padx=28, pady=(2, 0))
        tk.Frame(f, bg=LINE, height=1).pack(fill="x", padx=28)
        self._style_cards_holder = tk.Frame(f, bg=CARD)
        self._style_cards_holder.pack(fill="both", expand=True, padx=28,
                                      pady=16)
        select(STYLE_TABS[0][0])

    def _style_tab_selected(self, name: str) -> None:
        self._style_cat = dict(STYLE_TABS)[name]
        self._render_style_cards()

    def _render_style_cards(self) -> None:
        holder = self._style_cards_holder
        for w in holder.winfo_children():
            w.destroy()
        current, _ = self.app.db.style_for(self._style_cat)
        for key, title, sub, sample in STYLE_CARDS:
            selected = (key == current)
            card = tk.Frame(holder, bg=CARD2, highlightthickness=2,
                            highlightbackground=PULSE if selected else LINE,
                            cursor="hand2")
            card.pack(side="left", fill="both", expand=True, padx=(0, 14))
            tk.Label(card, text=title, bg=CARD2, fg=TEXT,
                     font=(self._disp, 14, "bold")).pack(anchor="w", padx=18,
                                                         pady=(18, 0))
            tk.Label(card, text=sub, bg=CARD2, fg=DIMT,
                     font=(FONT, 9)).pack(anchor="w", padx=18, pady=(2, 10))
            bubble = tk.Label(card, text=sample, bg=GROUND, fg=TEXT,
                              font=(FONT, 9), justify="left",
                              wraplength=190, padx=10, pady=8)
            bubble.pack(anchor="w", padx=18, pady=(4, 18))

            def _pick(_e=None, k=key):
                self.app.db.set_style(self._style_cat, k)
                self._render_style_cards()
            for w in (card, bubble, *card.winfo_children()):
                w.bind("<Button-1>", _pick)

    # ---- Settings ------------------------------------------------------------------------
    def _section(self, f, text) -> tk.Frame:
        eyebrow(f, text).pack(anchor="w", padx=28, pady=(18, 4))
        box = tk.Frame(f, bg=CARD)
        box.pack(fill="x", padx=28)
        return box

    def _radio(self, parent, text, var, value, command):
        return tk.Radiobutton(parent, text=text, variable=var, value=value,
                              command=command, bg=CARD, fg=TEXT,
                              selectcolor=GROUND, activebackground=CARD,
                              activeforeground=TEXT, highlightthickness=0,
                              anchor="w", font=(FONT, 10), cursor="hand2")

    def _check(self, parent, text, var, command):
        return tk.Checkbutton(parent, text=text, variable=var,
                              command=command, bg=CARD, fg=TEXT,
                              selectcolor=GROUND, activebackground=CARD,
                              activeforeground=TEXT, highlightthickness=0,
                              anchor="w", font=(FONT, 10), cursor="hand2")

    def _build_settings(self, f) -> None:
        a = self.app
        self._header(f, "Settings")

        box = self._section(f, "Speech engine")
        self.engine_var = tk.StringVar(value=a.cfg["asr"]["engine"])
        self._radio(box, "Whisper small.en — most accurate, ~1.4 s",
                    self.engine_var, "whisper",
                    lambda: a.set_engine("whisper")).pack(anchor="w")
        self._radio(box, "Parakeet 0.6B — fastest, ~0.3 s",
                    self.engine_var, "parakeet",
                    lambda: a.set_engine("parakeet")).pack(anchor="w")

        box = self._section(f, "Cleanup level")
        self.level_var = tk.StringVar(value=a.cfg["cleanup_level"])
        row = tk.Frame(box, bg=CARD)
        row.pack(anchor="w")
        for lv in ("off", "light", "medium", "high"):
            self._radio(row, lv.capitalize(), self.level_var, lv,
                        lambda: a.set_cleanup_level(self.level_var.get())
                        ).pack(side="left", padx=(0, 12))

        box = self._section(f, "While dictating")
        self.mute_sound_var = tk.BooleanVar(
            value=a.cfg["audio"].get("mute_during_dictation", True))
        self._check(box, "Mute other apps' sound (music, videos)",
                    self.mute_sound_var,
                    lambda: a.set_audio_option(
                        "mute_during_dictation", self.mute_sound_var.get())
                    ).pack(anchor="w")

        box = self._section(f, "Interface")
        self.overlay_var = tk.BooleanVar(value=a.cfg["ui"]["show_overlay"])
        self.widget_var = tk.BooleanVar(value=a.cfg["ui"]["show_widget"])

        def _overlay():
            a.cfg["ui"]["show_overlay"] = self.overlay_var.get()
            save_config(a.cfg)
        self._check(box, "Show the dictation pill while listening",
                    self.overlay_var, _overlay).pack(anchor="w")
        self._check(box, "Show the floating Flow bar on screen",
                    self.widget_var,
                    lambda: a.set_widget_visible(self.widget_var.get())
                    ).pack(anchor="w")

        box = self._section(f, "System")
        self.auto_var = tk.BooleanVar(value=a.autostart_enabled)
        self._check(box, "Start Local Flow when Windows starts",
                    self.auto_var, a.toggle_autostart).pack(anchor="w")
        row = tk.Frame(box, bg=CARD)
        row.pack(anchor="w", pady=(8, 0))
        btn_ghost(row, "Open data folder",
                  lambda: open_path(DATA_DIR)).pack(side="left")
        tk.Label(row, text=f"v{__version__}", bg=CARD, fg=DIMT,
                 font=(FONT, 8)).pack(side="left", padx=12)

        import sys
        if sys.platform == "darwin":
            hotkeys = (("⌃⌥ Ctrl+Option (hold)", "dictate"),
                       ("⌃⌥⌘ +Cmd (hold)", "command mode"),
                       ("⌃⌥Space", "hands-free toggle"),
                       ("⌥⇧Z", "paste last transcript"),
                       ("Esc", "cancel"))
        else:
            hotkeys = (("Ctrl+Win (hold)", "dictate"),
                       ("Ctrl+Win+Alt (hold)", "command mode"),
                       ("Ctrl+Win+Space", "hands-free toggle"),
                       ("Alt+Shift+Z", "paste last transcript"),
                       ("Esc", "cancel"))
        box = self._section(f, "Hotkeys")
        for combo, what in hotkeys:
            r = tk.Frame(box, bg=CARD)
            r.pack(anchor="w", fill="x")
            tk.Label(r, text=combo, bg=CARD, fg=FLOW, width=20, anchor="w",
                     font=(self._mono, 9)).pack(side="left")
            tk.Label(r, text=what, bg=CARD, fg=DIMT,
                     font=(FONT, 9)).pack(side="left")
