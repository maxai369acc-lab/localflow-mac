# Local Flow on macOS

This kit contains the full Local Flow source plus a one-command compiler.
Everything runs locally on the Mac — no accounts, no cloud.

## Compile the app (one command)

```bash
./compile-mac.sh
```

That installs the Python toolchain if needed, runs the tests, and compiles
the executable app bundle to **`dist/LocalFlow.app`**.

## First-run setup (once)

1. **Cleanup LLM server** (polishes your dictation):

   ```bash
   brew install llama.cpp
   mkdir -p "$HOME/Library/Application Support/LocalFlow/llama.cpp"
   ln -sf "$(which llama-server)" \
     "$HOME/Library/Application Support/LocalFlow/llama.cpp/llama-server"
   ```

2. **Models** (speech recognition + LLM, ~1.5 GB, one time):

   ```bash
   uv run localflow-setup
   ```

3. **Permissions** — open `dist/LocalFlow.app`; when macOS asks, grant
   **Microphone** and **Accessibility** (System Settings → Privacy &
   Security). Accessibility is required for global hotkeys and paste.

## Using it

| Keys | Action |
|---|---|
| hold **Ctrl + Option** | dictate, release to paste |
| hold **Ctrl + Option + Cmd** | command mode (transform selection) |
| **Ctrl + Option + Space** | hands-free toggle |
| **Option + Shift + Z** | paste last transcript |
| **Esc** | cancel |

The floating flow bar and the main window work the same as on Windows.
There is no menu-bar icon on macOS (by design); the flow bar is the control
surface. Data lives in `~/Library/Application Support/LocalFlow/`.

## Notes

- Apple Silicon and Intel both work; the app is compiled for the machine
  that runs `compile-mac.sh`.
- The app is unsigned. If Gatekeeper complains, right-click the app →
  Open → Open once, and macOS remembers.
