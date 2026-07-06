"""Paths, defaults and user settings for Local Flow.

Everything lives on D: per the machine constraints (C: is tight):
  D:\\AI\\local-flow   app data (config, db, audio, logs, bench)
  D:\\AI\\models       ASR + LLM model files
  D:\\AI\\llama.cpp    portable llama.cpp binaries (llama-server.exe)
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    # Keep every model/cache download off C: (machine constraint).
    os.environ.setdefault("HF_HOME", r"D:\AI\Caches\hf")
    _DEFAULT_DATA = Path(r"D:\AI\local-flow")
    MODELS_DIR = Path(r"D:\AI\models")
    LLAMA_DIR = Path(r"D:\AI\llama.cpp")
    _LLAMA_EXE = "llama-server.exe"
elif IS_MAC:
    _base = Path.home() / "Library" / "Application Support"
    _DEFAULT_DATA = _base / "LocalFlow"
    MODELS_DIR = _DEFAULT_DATA / "models"
    LLAMA_DIR = _DEFAULT_DATA / "llama.cpp"
    _LLAMA_EXE = "llama-server"
else:  # linux/other
    _DEFAULT_DATA = Path.home() / ".local" / "share" / "localflow"
    MODELS_DIR = _DEFAULT_DATA / "models"
    LLAMA_DIR = _DEFAULT_DATA / "llama.cpp"
    _LLAMA_EXE = "llama-server"

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

DATA_DIR = Path(os.environ.get("LOCALFLOW_DATA", str(_DEFAULT_DATA)))
AUDIO_DIR = DATA_DIR / "audio"
LOG_DIR = DATA_DIR / "logs"
BENCH_DIR = DATA_DIR / "bench"
DB_PATH = DATA_DIR / "localflow.db"
CONFIG_PATH = DATA_DIR / "config.json"

WHISPER_DIR = MODELS_DIR / "whisper"


def open_path(path) -> None:
    """Open a folder/file in the OS file manager."""
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606
    elif IS_MAC:
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

DEFAULTS: dict = {
    "asr": {
        "engine": "whisper",          # whisper | parakeet
        "whisper_model": "small.en",  # tier B per spec; medium.en = tier C
        "compute_type": "int8",
        "cpu_threads": 6,             # i5-12400 physical cores
        "beam_size": 1,
        "language": "en",
    },
    "llm": {
        "enabled": True,
        "server_exe": str(LLAMA_DIR / _LLAMA_EXE),
        "model_path": str(MODELS_DIR / "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
        "port": 8735,
        "ctx": 4096,
        "threads": 6,
        "timeout_s": 25,
        "autostart": True,
        "stop_on_exit": True,
    },
    "cleanup_level": "medium",        # off | light | medium | high
    "hotkeys": {
        "ptt": "ctrl+win (hold)",     # informational; tracked natively
        "handsfree": "ctrl+windows+space",
        "paste_last": "alt+shift+z",
    },
    "audio": {
        "device": None,               # sounddevice index or None = default
        "samplerate": 16000,
        "max_seconds": 180,
        "min_utterance_ms": 300,
        "silence_rms": 0.0035,
        "mute_during_dictation": True,  # mute other apps' audio while recording
    },
    "paste": {
        "restore_clipboard": True,
        "restore_delay_ms": 900,
        "pre_paste_delay_ms": 60,
    },
    "context": {
        "enabled": True,
        "read_window_title": True,
    },
    "ui": {
        "show_overlay": True,
        "show_widget": True,          # always-on-screen floating orb
        "widget_pos": None,           # [x, y] saved after dragging
    },
    "dev": {
        "enabled": True,
        "workspace_folders": [],      # user-granted roots for identifier scan
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def ensure_dirs() -> None:
    for d in (DATA_DIR, AUDIO_DIR, LOG_DIR, BENCH_DIR, MODELS_DIR, WHISPER_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return _deep_merge(DEFAULTS, user)
        except (json.JSONDecodeError, OSError):
            pass
    save_config(DEFAULTS)
    return copy.deepcopy(DEFAULTS)


def save_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
