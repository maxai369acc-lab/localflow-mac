#!/usr/bin/env bash
# Local Flow — macOS compile script.
# Run this ON A MAC from the unzipped folder:  ./compile-mac.sh
# Produces the executable app at  dist/LocalFlow.app
set -euo pipefail

echo "== Local Flow macOS build =="

# 1) uv (Python package manager) — installed to ~/.local/bin if missing
if ! command -v uv >/dev/null 2>&1; then
  echo "-- installing uv…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# 2) dependencies (pulls the macOS extras: pynput, pyobjc)
echo "-- syncing dependencies…"
uv sync
uv pip install pyinstaller

# 3) sanity check before building
echo "-- running tests…"
uv run pytest -q

# 4) compile the app bundle
echo "-- compiling LocalFlow.app…"
uv run pyinstaller --noconfirm --windowed --name LocalFlow --paths src \
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all av \
  --collect-all onnx_asr --collect-all onnxruntime --collect-all pystray \
  launcher.py

echo ""
echo "== Done: dist/LocalFlow.app =="
echo "Next steps (see README-MAC.md):"
echo "  1. brew install llama.cpp   (the cleanup LLM server)"
echo "  2. mkdir -p \"\$HOME/Library/Application Support/LocalFlow/llama.cpp\""
echo "     ln -sf \"\$(which llama-server)\" \"\$HOME/Library/Application Support/LocalFlow/llama.cpp/llama-server\""
echo "  3. uv run localflow-setup   (downloads the speech + LLM models)"
echo "  4. Open dist/LocalFlow.app — grant Microphone and Accessibility"
echo "     permissions when macOS asks (System Settings > Privacy)."
