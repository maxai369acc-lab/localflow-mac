# Local Flow for macOS

Local, private, system-wide AI dictation — hold a hotkey, speak, release,
and polished text is pasted into whatever app has focus. Runs **fully
offline**: speech recognition (faster-whisper) and the cleanup LLM
(Qwen2.5 via llama.cpp) both run on your Mac. Nothing leaves the machine.

> **Windows user?** This repo is the macOS build. The Windows app (with
> prebuilt downloads and auto-updates) lives at
> [maxai369acc-lab/localflow](https://github.com/maxai369acc-lab/localflow).

## Build it (one command, ~10 minutes)

```bash
git clone https://github.com/maxai369acc-lab/localflow-mac.git
cd localflow-mac
./compile-mac.sh
```

The script installs the `uv` Python toolchain if needed, runs the test
suite, and compiles the app bundle to **`dist/LocalFlow.app`**.

## First-run setup (once)

Full step-by-step instructions are in **[README-MAC.md](README-MAC.md)** —
in short:

1. `brew install llama.cpp` and symlink `llama-server` into the app's
   support folder (exact commands in README-MAC.md)
2. `uv run localflow-setup` — downloads the speech + LLM models (~1.5 GB)
3. Open `dist/LocalFlow.app` and grant **Microphone** and **Accessibility**
   permissions when macOS asks

## Hotkeys (macOS)

| Keys | Action |
|---|---|
| hold **Ctrl + Option** | dictate, release to paste |
| **Ctrl + Option + Space** | hands-free toggle |
| hold **Ctrl + Option + Cmd** | command mode (transform selected text by voice) |
| **Option + Shift + Z** | paste last transcript |
| **Esc** | cancel |

## Status

The macOS port is code-complete but still being validated on real
hardware — if something misbehaves, please open an issue with the output
of `./compile-mac.sh` and `uv run localflow --selftest`.

Note: over-the-air auto-update is Windows-only for now; on macOS you
update by pulling this repo and re-running `./compile-mac.sh`.
