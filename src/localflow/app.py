"""Local Flow — main application orchestrator.

State machine: starting -> idle <-> recording -> processing -> idle
Pipeline per utterance: capture -> save wav -> ASR -> rules -> (LLM polish)
-> paste into active field -> history.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

from . import __version__
from .audio import Recorder, default_input_device, list_input_devices, peak_rms, save_wav
from .config import AUDIO_DIR, DATA_DIR, LOG_DIR, load_config, save_config
from .context import AppContext, get_foreground_context
from .db import DB
from .ducking import SystemMuter
from .hotkeys import HotkeyManager
from .insert import (ClipboardError, capture_selection, get_clipboard_text,
                     insert_text, set_clipboard_text)
from .llm_server import LlamaServerManager
from .polish import LlamaClient, PolishError
from .rules import apply_rules, chat_trailing_period, remove_fillers
from .ui import Tray, Ui
from .updater import UpdateChecker, UpdateInfo, Updater, is_packaged

log = logging.getLogger("localflow")

STARTING, IDLE, RECORDING, PROCESSING = "starting", "idle", "recording", "processing"


def setup_logging(verbose: bool = False) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=1_000_000,
                             backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)


class App:
    def __init__(self, cfg: dict, db: DB):
        self.cfg = cfg
        self.db = db
        self.state = STARTING
        self.session: dict | None = None
        self._lock = threading.RLock()
        self._max_timer: threading.Timer | None = None
        self._warn_ts = 0.0
        self._quitting = False

        self.asr = None
        self.asr_error: str | None = None
        self.update_available: UpdateInfo | None = None
        self._update_busy = False
        self.llm_ready = False
        self.dev_identifiers: list[str] = []
        self._dev_pairs: list[tuple[str, str]] = []

        self.ui = Ui()
        self.ui.on_widget_click = self._widget_clicked
        self.ui.on_widget_move = self._save_widget_pos
        self.ui.configure_widget(cfg["ui"].get("show_widget", True),
                                 cfg["ui"].get("widget_pos"))
        self.tray = Tray(self)
        self.recorder = Recorder(device=cfg["audio"]["device"],
                                 on_level=self.ui.set_level)
        self.muter = SystemMuter()
        self.llm_mgr = LlamaServerManager(cfg)
        self.llama = LlamaClient(port=cfg["llm"]["port"],
                                 timeout_s=cfg["llm"]["timeout_s"])
        self.hotkeys = HotkeyManager(
            on_ptt_start=lambda: self._spawn(self._begin, "ptt"),
            on_ptt_stop=lambda: self._spawn(self._end, "ptt"),
            on_ptt_abort=lambda: self._spawn(self._cancel, "shortcut"),
            on_handsfree_toggle=lambda: self._spawn(self._handsfree),
            on_cancel=lambda: self._spawn(self._cancel, "esc"),
            on_paste_last=lambda: self._spawn(self._paste_last),
            on_command_start=lambda: self._spawn(self._to_command),
        )

    @staticmethod
    def _spawn(fn, *args) -> None:
        threading.Thread(target=fn, args=args, daemon=True).start()

    # ---- tray-facing properties/actions ---------------------------------
    @property
    def status_text(self) -> str:
        if self.state == STARTING:
            return "loading speech model…"
        bits = [self.state, f"cleanup: {self.cfg['cleanup_level']}"]
        if (self.cfg["llm"]["enabled"] and self.cfg["cleanup_level"] in ("medium", "high")
                and not self.llm_ready):
            bits.append("LLM offline")
        return ", ".join(bits)

    @property
    def handsfree_active(self) -> bool:
        s = self.session
        return bool(s and s.get("mode") == "handsfree" and self.state == RECORDING)

    def set_cleanup_level(self, level: str) -> None:
        self.cfg["cleanup_level"] = level
        save_config(self.cfg)
        self.tray.set_state(self.state)

    def set_audio_option(self, key: str, on: bool) -> None:
        """Flip a dictation-time audio option (mute_during_dictation /
        mute_mic_for_others) and persist it."""
        self.cfg["audio"][key] = bool(on)
        save_config(self.cfg)

    def set_engine(self, name: str) -> None:
        """Switch ASR engine (whisper|parakeet); loads in the background."""
        if name == self.cfg["asr"]["engine"]:
            return
        self.cfg["asr"]["engine"] = name
        save_config(self.cfg)
        self._spawn(self._reload_asr, name)

    def _reload_asr(self, name: str) -> None:
        try:
            from .asr import make_engine
            t0 = time.perf_counter()
            eng = make_engine(self.cfg)
            eng.warmup()
            self.asr = eng   # old engine keeps serving in-flight calls
            self.asr_error = None
            log.info("ASR switched to %s in %.1fs", eng.name,
                     time.perf_counter() - t0)
            self.tray.notify("Local Flow", f"Speech engine ready: {eng.name}")
        except Exception as e:
            log.exception("engine switch failed")
            self.tray.notify("Engine switch failed",
                             f"{e}\nStill using the previous engine.")
        self.tray.set_state(self.state)

    def open_main(self, tab: str = "recent") -> None:
        from .mainwin import MainWindow
        self.ui.call(lambda: MainWindow.open(self, tab))

    def open_history(self) -> None:
        self.open_main("recent")

    def open_dictionary(self) -> None:
        self.open_main("dictionary")

    def open_snippets(self) -> None:
        self.open_main("snippets")

    def _widget_clicked(self, action: str) -> None:
        if action == "mic":
            self.on_handsfree_toggle()
        else:
            self.open_main()

    def set_widget_visible(self, show: bool) -> None:
        self.cfg["ui"]["show_widget"] = bool(show)
        save_config(self.cfg)
        self.ui.configure_widget(show, self.cfg["ui"].get("widget_pos"))

    def _save_widget_pos(self, x: int, y: int) -> None:
        self.cfg["ui"]["widget_pos"] = [int(x), int(y)]
        save_config(self.cfg)

    # tray menu handlers reuse hotkey entry points
    def on_handsfree_toggle(self) -> None:
        self._spawn(self._handsfree)

    def on_paste_last(self) -> None:
        self._spawn(self._paste_last)

    def on_copy_last(self) -> None:
        self._spawn(self._copy_last)

    # ---- background init -------------------------------------------------
    def start_background_init(self) -> None:
        self._spawn(self._load_asr)
        if self.cfg["llm"]["enabled"] and self.cfg["llm"].get("autostart", True):
            self._spawn(self._start_llm)
        if self.cfg["dev"]["enabled"] and self.cfg["dev"]["workspace_folders"]:
            self._spawn(self._scan_dev)
        if (sys.platform == "win32" and is_packaged()
                and self.cfg["update"]["check_enabled"]
                and self.cfg["update"]["repo"]):
            # the update feed only carries Windows builds; Mac updates by recompiling
            self._spawn(self._update_check_loop)

    # ---- auto-update -------------------------------------------------------
    def _update_check_loop(self) -> None:
        checker = UpdateChecker(self.cfg["update"]["repo"], __version__)
        time.sleep(30)  # let startup (ASR/LLM load) settle first
        while not self._quitting:
            info = checker.check()
            if info and info != self.update_available:
                self.update_available = info
                log.info("update available: v%s", info.version)
                self.tray.notify(f"Local Flow v{info.version} is available",
                                 "Right-click the tray icon and choose "
                                 "'Install update' when convenient.")
                self.tray.set_state(self.state)  # refresh menu
            time.sleep(24 * 3600)

    def install_update(self) -> None:
        self._spawn(self._install_update)

    def _install_update(self) -> None:
        info = self.update_available
        if info is None or self._update_busy:
            return
        self._update_busy = True
        try:
            self.tray.notify("Downloading update…",
                             f"Local Flow v{info.version} is downloading in "
                             "the background.")
            updater = Updater(DATA_DIR / "updates")
            staged = updater.download_and_stage(info)
            log.info("update v%s staged at %s", info.version, staged)
            updater.apply_and_restart(staged, self.quit)
        except Exception as e:
            log.exception("update install failed")
            self.tray.notify("Update failed",
                             f"{e}\nLocal Flow keeps running on v{__version__}.")
            self._update_busy = False

    def _scan_dev(self) -> None:
        try:
            from .devmode import scan_identifiers, sound_alike_pairs
            idents = scan_identifiers(self.cfg["dev"]["workspace_folders"])
            self.dev_identifiers = idents
            self._dev_pairs = sound_alike_pairs(idents)
        except Exception:
            log.exception("devmode identifier scan failed")

    def add_workspace_folder(self) -> None:
        def _ask():
            from tkinter import filedialog
            folder = filedialog.askdirectory(
                title="Grant a workspace folder to Local Flow")
            if folder:
                folders = self.cfg["dev"].setdefault("workspace_folders", [])
                if folder not in folders:
                    folders.append(folder)
                    save_config(self.cfg)
                self._spawn(self._scan_dev)
        self.ui.call(_ask)

    def rescan_dev(self) -> None:
        self._spawn(self._scan_dev)

    # ---- autostart -------------------------------------------------------
    _RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _PLIST = Path.home() / "Library" / "LaunchAgents" / "com.localflow.app.plist"

    @staticmethod
    def _autostart_command() -> list[str]:
        if getattr(sys, "frozen", False):  # packaged app: point at ourselves
            return [sys.executable]
        if sys.platform == "win32":
            return [str(Path(sys.executable).with_name("pythonw.exe")),
                    "-m", "localflow"]
        return [sys.executable, "-m", "localflow"]

    @property
    def autostart_enabled(self) -> bool:
        if sys.platform == "win32":
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY) as k:
                    winreg.QueryValueEx(k, "LocalFlow")
                    return True
            except OSError:
                return False
        return self._PLIST.exists()

    def toggle_autostart(self) -> None:
        try:
            if sys.platform == "win32":
                self._toggle_autostart_win()
            else:
                self._toggle_autostart_mac()
        except OSError as e:
            log.error("autostart toggle failed: %s", e)
            self.tray.notify("Autostart failed", str(e))
        self.tray.set_state(self.state)

    def _toggle_autostart_win(self) -> None:
        import winreg
        cmd = self._autostart_command()
        value = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if self.autostart_enabled:
                winreg.DeleteValue(k, "LocalFlow")
                log.info("autostart disabled")
            else:
                winreg.SetValueEx(k, "LocalFlow", 0, winreg.REG_SZ, value)
                log.info("autostart enabled: %s", value)

    def _toggle_autostart_mac(self) -> None:
        if self.autostart_enabled:
            self._PLIST.unlink(missing_ok=True)
            log.info("autostart disabled")
            return
        import plistlib
        self._PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(self._PLIST, "wb") as f:
            plistlib.dump({
                "Label": "com.localflow.app",
                "ProgramArguments": self._autostart_command(),
                "RunAtLoad": True,
                "ProcessType": "Interactive",
            }, f)
        log.info("autostart enabled: %s", self._PLIST)

    def _load_asr(self) -> None:
        try:
            from .asr import make_engine
            t0 = time.perf_counter()
            eng = make_engine(self.cfg)
            eng.warmup()
            self.asr = eng
            log.info("ASR ready (%s) in %.1fs", eng.name, time.perf_counter() - t0)
        except Exception as e:
            self.asr_error = str(e)
            log.exception("ASR load failed")
            self.tray.notify("Local Flow — speech model failed",
                             f"{e}\nRun 'localflow-setup' to download models.")
        finally:
            with self._lock:
                if self.state == STARTING:
                    self.state = IDLE
            self.tray.set_state("idle" if self.asr else "error")
            if self.asr:
                mic = default_input_device() or "default microphone"
                self.tray.notify("Local Flow is ready",
                                 f"Hold Ctrl+Win and speak ({mic}). "
                                 "Ctrl+Win+Space = hands-free. Esc = cancel.")

    def _start_llm(self) -> None:
        try:
            self.llm_mgr.ensure_running()
            self.llm_ready = True
            log.info("llama-server healthy on port %s", self.cfg["llm"]["port"])
        except Exception as e:
            log.warning("cleanup LLM unavailable: %s", e)
            self.tray.notify("Cleanup LLM unavailable",
                             f"{e}\nDictation still works with rule-based cleanup.")
        self.tray.set_state(self.state)

    # ---- session control ---------------------------------------------------
    def _warn_throttled(self, title: str, msg: str) -> None:
        now = time.time()
        if now - self._warn_ts > 6:
            self._warn_ts = now
            self.tray.notify(title, msg)

    def _begin(self, mode: str) -> None:
        with self._lock:
            if self._quitting or self.state == RECORDING:
                return
            if self.state != IDLE or self.asr is None:
                self._warn_throttled("Local Flow is still warming up",
                                     "The speech model is loading — try again shortly.")
                return
            if self.cfg["context"]["enabled"]:
                ctx = get_foreground_context(self.db, self.cfg["context"]["read_window_title"])
            else:
                ctx = AppContext()
            try:
                self.recorder.start()
            except RuntimeError as e:
                log.error("mic open failed: %s", e)
                self.tray.notify("Microphone error", str(e))
                return
            self.state = RECORDING
            if self.cfg["audio"].get("mute_during_dictation", True):
                self.muter.mute_others()
            self.session = {"mode": mode, "ctx": ctx, "t0": time.monotonic()}
            self._max_timer = threading.Timer(
                float(self.cfg["audio"]["max_seconds"]),
                lambda: self._spawn(self._end, "timer"))
            self._max_timer.daemon = True
            self._max_timer.start()
        self.hotkeys.enable_cancel()
        if self.cfg["ui"]["show_overlay"]:
            label = "Listening… (hands-free)" if mode == "handsfree" else "Listening…"
            self.ui.show_listening(label)
        self.tray.set_state("recording")
        log.info("recording started (%s) in %s [%s]", mode, ctx.process, ctx.category)

    def _end(self, origin: str) -> None:
        with self._lock:
            if self.state != RECORDING or self.session is None:
                return
            sess = self.session
            if origin == "ptt" and sess["mode"] == "handsfree":
                return  # hands-free ignores the key release (command does not)
            if origin == "handsfree" and sess["mode"] != "handsfree":
                return
            if self._max_timer:
                self._max_timer.cancel()
                self._max_timer = None
            elapsed_ms = (time.monotonic() - sess["t0"]) * 1000
            audio = self.recorder.stop()
            self.muter.restore()
            if origin == "ptt" and elapsed_ms < float(self.cfg["audio"]["min_utterance_ms"]):
                self.state = IDLE
                self.session = None
                quiet_tap = True
            else:
                self.state = PROCESSING
                self.session = None
                quiet_tap = False
        self.hotkeys.disable_cancel()
        if quiet_tap:
            self.ui.hide_overlay()
            self.tray.set_state("idle")
            return
        self.ui.show_processing()
        self.tray.set_state("processing")
        self._spawn(self._process, audio, sess)

    def _to_command(self) -> None:
        """Alt joined a Ctrl+Win hold: this session is a spoken command."""
        with self._lock:
            if self.state != RECORDING or self.session is None:
                return
            if self.session["mode"] == "command":
                return
            self.session["mode"] = "command"
        if self.cfg["ui"]["show_overlay"]:
            self.ui.show_listening("Command…", kind="command")
        log.info("session upgraded to command mode")

    def _handsfree(self) -> None:
        action = None
        with self._lock:
            if self.state == RECORDING and self.session:
                if self.session["mode"] == "ptt":
                    self.session["mode"] = "handsfree"
                    self.ui.show_listening("Listening… (hands-free)")
                    return
                action = "end"
            elif self.state == IDLE:
                action = "begin"
        if action == "end":
            self._end("handsfree")
        elif action == "begin":
            self._begin("handsfree")

    def _cancel(self, origin: str) -> None:
        with self._lock:
            if self.state != RECORDING:
                return
            if self._max_timer:
                self._max_timer.cancel()
                self._max_timer = None
            self.recorder.cancel()
            self.muter.restore()
            self.state = IDLE
            self.session = None
        self.hotkeys.disable_cancel()
        self.ui.hide_overlay()
        self.tray.set_state("idle")
        log.info("session cancelled (%s)", origin)

    # ---- the pipeline ------------------------------------------------------
    def _process(self, audio, sess: dict) -> None:
        if sess["mode"] == "command":
            return self._process_command(audio, sess)
        ctx: AppContext = sess["ctx"]
        hid = None
        try:
            dur = len(audio) / 16000.0
            if dur < 0.25 or peak_rms(audio) < float(self.cfg["audio"]["silence_rms"]):
                log.info("no speech detected (dur=%.2fs)", dur)
                return
            wav_path = AUDIO_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{sess['mode']}.wav"
            self._spawn(save_wav, audio, wav_path)  # never lose audio
            hid = self.db.history_start(ctx.process, sess["mode"], str(wav_path))

            entries = self.db.dictionary_entries()
            dictionary = [t for t, _ in entries]
            sound_alikes = [(v.strip(), t) for t, h in entries if h
                            for v in h.split(",") if v.strip()]
            llm_dictionary = [f"{t} (sounds like: {h})" if h else t
                              for t, h in entries]
            if ctx.category == "code" and self.dev_identifiers:
                dictionary = dictionary + self.dev_identifiers
                sound_alikes = sound_alikes + self._dev_pairs
                llm_dictionary = llm_dictionary + self.dev_identifiers
            res = self.asr.transcribe(audio, dictionary=dictionary)
            raw = res.raw_text
            log.info("ASR %dms (rtf %.2f): %r", res.asr_ms, res.rtf, raw[:150])
            if not raw.strip():
                self.db.history_finish(hid, "", None, res.asr_ms, None, False,
                                       "no speech recognized")
                return

            level = self.cfg["cleanup_level"]
            snippets = self.db.snippets()
            rres = apply_rules(raw, level=level, category=ctx.category,
                               dictionary=dictionary, snippets=snippets,
                               sound_alikes=sound_alikes)
            text = rres.text
            llm_ms = None
            if level in ("medium", "high") and self.cfg["llm"]["enabled"]:
                if self.llama.health():
                    self.llm_ready = True
                    style, custom = self.db.style_for(ctx.category)
                    try:
                        polished, llm_ms = self.llama.polish(
                            text, level=level, category=ctx.category, style=style,
                            dictionary=llm_dictionary, snippets=snippets,
                            custom_rules=custom)
                        if polished.strip():
                            text, _ = chat_trailing_period(polished.strip(), ctx.category)
                    except PolishError as e:
                        log.warning("polish failed, using rule-cleaned text: %s", e)
                else:
                    self.llm_ready = False
                    self._warn_throttled(
                        "Cleanup LLM offline",
                        "Pasted rule-cleaned text. llama-server is not responding.")

            pasted = False
            try:
                pasted = insert_text(
                    text,
                    strategy=ctx.paste_strategy,
                    restore_clipboard=self.cfg["paste"]["restore_clipboard"],
                    restore_delay_ms=int(self.cfg["paste"]["restore_delay_ms"]),
                    pre_paste_delay_ms=int(self.cfg["paste"]["pre_paste_delay_ms"]),
                    send_enter=rres.send_enter,
                    pause_hook=self.hotkeys.pause,
                    resume_hook=self.hotkeys.resume,
                )
            except ClipboardError as e:
                log.error("clipboard failure: %s", e)
                self.tray.notify("Paste failed",
                                 "Clipboard was busy. Text is saved — press "
                                 "Alt+Shift+Z to paste it.")
            if pasted:
                self.ui.show_success(len(text.split()))
            else:
                self.ui.show_error("paste failed — Alt+Shift+Z to retry")
            self.db.history_finish(hid, raw, text, res.asr_ms, llm_ms, pasted)
            log.info("done: asr=%dms llm=%sms pasted=%s edits=%s",
                     res.asr_ms, llm_ms, pasted, rres.edits)
        except Exception as e:
            log.exception("dictation pipeline failed")
            if hid is not None:
                try:
                    self.db.history_finish(hid, None, None, None, None, False, str(e))
                except Exception:
                    pass
            self.ui.show_error("dictation failed")
            self.tray.notify("Dictation failed", str(e))
        finally:
            with self._lock:
                if self.state == PROCESSING:
                    self.state = IDLE
            self.ui.hide_overlay()
            self.tray.set_state("idle")

    def _process_command(self, audio, sess: dict) -> None:
        """Command Mode: transform the selection (or generate text) per the
        spoken instruction and paste the result."""
        ctx: AppContext = sess["ctx"]
        hid = None
        try:
            dur = len(audio) / 16000.0
            if dur < 0.25 or peak_rms(audio) < float(self.cfg["audio"]["silence_rms"]):
                log.info("command: no speech detected (dur=%.2fs)", dur)
                return

            # Grab the selection first, while the target app is still fresh.
            # Never in terminals: Ctrl+C is an interrupt there.
            selection = None
            if ctx.category != "terminal":
                try:
                    selection = capture_selection(pause_hook=self.hotkeys.pause,
                                                  resume_hook=self.hotkeys.resume)
                except Exception:
                    log.exception("selection capture failed; treating as none")

            wav_path = AUDIO_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-command.wav"
            self._spawn(save_wav, audio, wav_path)
            hid = self.db.history_start(ctx.process, "command", str(wav_path))

            dictionary = self.db.dictionary()
            res = self.asr.transcribe(audio, dictionary=dictionary)
            spoken = res.raw_text.strip()
            log.info("command ASR %dms: %r (selection: %d chars)",
                     res.asr_ms, spoken[:150], len(selection or ""))
            if not spoken:
                self.db.history_finish(hid, "", None, res.asr_ms, None, False,
                                       "no speech recognized")
                return
            spoken, _ = remove_fillers(spoken)

            if not (self.cfg["llm"]["enabled"] and self.llama.health()):
                self.llm_ready = False
                self.db.history_finish(hid, spoken, None, res.asr_ms, None,
                                       False, "cleanup LLM offline")
                self.ui.show_error("Command Mode needs the LLM")
                self._warn_throttled("Command Mode unavailable",
                                     "llama-server is not responding.")
                return
            self.llm_ready = True

            result, llm_ms = self.llama.command(selection or "", spoken)
            if not result.strip():
                self.db.history_finish(hid, spoken, None, res.asr_ms, None,
                                       False, "empty LLM result")
                self.ui.show_error("command produced nothing")
                return

            pasted = False
            try:
                pasted = insert_text(
                    result,
                    strategy=ctx.paste_strategy,
                    restore_clipboard=self.cfg["paste"]["restore_clipboard"],
                    restore_delay_ms=int(self.cfg["paste"]["restore_delay_ms"]),
                    pre_paste_delay_ms=int(self.cfg["paste"]["pre_paste_delay_ms"]),
                    pause_hook=self.hotkeys.pause,
                    resume_hook=self.hotkeys.resume,
                )
            except ClipboardError as e:
                log.error("clipboard failure: %s", e)
                self.tray.notify("Paste failed",
                                 "Clipboard was busy. Text is saved — press "
                                 "Alt+Shift+Z to paste it.")
            if pasted:
                self.ui.show_success(len(result.split()))
            else:
                self.ui.show_error("paste failed — Alt+Shift+Z to retry")
            self.db.history_finish(hid, spoken, result, res.asr_ms, llm_ms, pasted)
            log.info("command done: asr=%dms llm=%dms pasted=%s",
                     res.asr_ms, llm_ms, pasted)
        except PolishError as e:
            log.warning("command LLM failed: %s", e)
            if hid is not None:
                try:
                    self.db.history_finish(hid, None, None, None, None, False, str(e))
                except Exception:
                    pass
            self.ui.show_error("command failed")
        except Exception as e:
            log.exception("command pipeline failed")
            if hid is not None:
                try:
                    self.db.history_finish(hid, None, None, None, None, False, str(e))
                except Exception:
                    pass
            self.ui.show_error("command failed")
            self.tray.notify("Command failed", str(e))
        finally:
            with self._lock:
                if self.state == PROCESSING:
                    self.state = IDLE
            self.ui.hide_overlay()
            self.tray.set_state("idle")

    def _paste_last(self) -> None:
        text = self.db.last_final_text()
        if not text:
            self.tray.notify("Local Flow", "No transcript in history yet.")
            return
        ctx = get_foreground_context(self.db, False)
        try:
            insert_text(text, strategy=ctx.paste_strategy,
                        restore_clipboard=self.cfg["paste"]["restore_clipboard"],
                        restore_delay_ms=int(self.cfg["paste"]["restore_delay_ms"]),
                        pause_hook=self.hotkeys.pause,
                        resume_hook=self.hotkeys.resume)
        except ClipboardError:
            self.tray.notify("Paste failed", "Clipboard busy — try again.")

    def _copy_last(self) -> None:
        text = self.db.last_final_text()
        if not text:
            self.tray.notify("Local Flow", "No transcript in history yet.")
            return
        try:
            set_clipboard_text(text)
        except ClipboardError:
            self.tray.notify("Copy failed", "Clipboard busy — try again.")
            return
        self.tray.notify("Copied to clipboard", text[:120])

    # ---- shutdown ------------------------------------------------------------
    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        log.info("shutting down")

        def _shutdown():
            for step in (self.hotkeys.stop, self.recorder.cancel, self.muter.restore):
                try:
                    step()
                except Exception:
                    pass
            if self.cfg["llm"].get("stop_on_exit", True):
                try:
                    self.llm_mgr.stop()
                except Exception:
                    pass
            self.tray.stop()
            self.ui.quit()

        threading.Thread(target=_shutdown, daemon=True).start()


# ---- single instance ---------------------------------------------------------

def acquire_single_instance():
    if sys.platform == "win32":
        import win32api
        import win32event
        import winerror
        handle = win32event.CreateMutex(None, False, "Local\\LocalFlowSingleton")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return None
        return handle
    # POSIX: hold an exclusive lock on a file in the data dir
    import fcntl
    from .config import ensure_dirs
    ensure_dirs()
    f = open(DATA_DIR / "localflow.lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f  # keep the handle alive to hold the lock


# ---- selftest ------------------------------------------------------------------

def selftest(cfg: dict, full: bool = False) -> int:
    import numpy as np
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn):
        try:
            detail = fn() or "ok"
            results.append((name, True, str(detail)))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {e}"))

    check("data dirs", lambda: str(DATA_DIR))

    def _mics():
        devs = list_input_devices()
        if not devs:
            raise RuntimeError("no input devices found")
        return f"{len(devs)} input device(s); default: {default_input_device()}"
    check("microphones", _mics)

    def _record():
        r = Recorder()
        r.start()
        time.sleep(0.4)
        audio = r.stop()
        return f"captured {len(audio)} samples @16k"
    check("record 0.4s", _record)

    def _asr():
        from .asr import make_engine
        eng = make_engine(cfg)
        eng.warmup()
        res = eng.transcribe(np.zeros(int(16000 * 0.5), dtype=np.float32))
        return f"{eng.name} loaded; silence -> {res.raw_text!r} in {res.asr_ms}ms"
    check("ASR engine", _asr)

    def _rules():
        r = apply_rules("um hello uh world", level="light")
        assert r.text == "Hello world", r.text
        return "rule cleanup ok"
    check("rules", _rules)

    def _clip():
        prev, restorable = get_clipboard_text()
        set_clipboard_text("localflow-selftest")
        got, _ = get_clipboard_text()
        if prev is not None and restorable:
            set_clipboard_text(prev)
        assert got == "localflow-selftest"
        return "clipboard round-trip ok"
    check("clipboard", _clip)

    def _hook():
        if sys.platform == "win32":
            import keyboard
            h = keyboard.hook(lambda e: None)
            keyboard.unhook(h)
            return "global keyboard hook ok"
        from pynput import keyboard as pk
        lis = pk.Listener(on_press=lambda k: None)
        lis.start()
        lis.stop()
        return "pynput listener ok (grant Accessibility permission)"
    check("keyboard hook", _hook)

    def _llm_assets():
        mgr = LlamaServerManager(cfg)
        err = mgr.preflight_error()
        if err:
            raise RuntimeError(err)
        return f"{mgr.exe.name} + {mgr.model.name} present"
    check("LLM assets", _llm_assets)

    if full:
        def _llm_round_trip():
            mgr = LlamaServerManager(cfg)
            started_here = not mgr.health()
            mgr.ensure_running()
            client = LlamaClient(port=cfg["llm"]["port"], timeout_s=60)
            out, ms = client.polish(
                "um lets do coffee at 2 actually 3",
                level="medium", category="chat", style="casual",
                dictionary=[], snippets=[])
            if started_here and cfg["llm"].get("stop_on_exit", True):
                mgr.stop()
            return f"polish {ms}ms -> {out!r}"
        check("LLM round-trip", _llm_round_trip)

    print("\nLocal Flow selftest")
    print("-" * 60)
    ok_all = True
    for name, ok, detail in results:
        ok_all &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<16} {detail}")
    print("-" * 60)
    print("ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED")
    return 0 if ok_all else 2


# ---- entry point -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="localflow",
                                     description="Local, private, system-wide AI dictation.")
    parser.add_argument("--selftest", action="store_true",
                        help="check mic/models/clipboard/hooks and exit")
    parser.add_argument("--full", action="store_true",
                        help="with --selftest: also start llama-server and test polish")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    cfg = load_config()

    if args.selftest:
        return selftest(cfg, full=args.full)

    mutex = acquire_single_instance()
    if mutex is None:
        print("Local Flow is already running (see the system tray).")
        return 1

    db = DB()
    app = App(cfg, db)
    app.tray.start()
    app.hotkeys.start()
    app.start_background_init()
    log.info("Local Flow %s started", __version__)
    try:
        app.ui.run_forever()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
