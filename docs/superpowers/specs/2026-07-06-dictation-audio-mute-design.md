# System-audio mute during dictation — design

Date: 2026-07-06
Status: approved

## Goal

When a dictation session starts, fully mute all other applications' audio
(YouTube, Spotify, games, …). When the session ends — by key release,
hands-free toggle, Esc cancel, max-time timer, or app quit — restore every
application's audio to exactly the state it was in before.

## Decisions (from brainstorming)

- **Fully mute**, not duck: background audio must not bleed into the mic.
- **Approach A**: per-app WASAPI session mute via `pycaw`, with exact
  per-session state restore. Rejected: master-endpoint mute (blunt, risky on
  crash), Windows communications ducking (not exposed by sounddevice, only
  attenuates ~80%).
- **On by default**, disable via `audio.mute_during_dictation: false`.
- Restore happens when **recording stops**, not when text is inserted —
  audio resumes while transcription runs.

## Components

### `src/localflow/ducking.py` (new)

`SystemMuter` class:

- `mute_others() -> None` — enumerate active audio sessions via
  `pycaw.AudioUtilities.GetAllSessions()`, snapshot each session's current
  mute state, then mute every session whose PID is not our own process.
  No-op if already muted (re-entrant safe).
- `restore() -> None` — restore each snapshotted session to its recorded
  mute state; clear the snapshot. Idempotent: safe to call multiple times
  and from multiple exit paths.

Platform/failure behaviour:

- Non-Windows (`sys.platform != "win32"`): both methods are silent no-ops.
- `pycaw` missing or COM errors: log one warning, degrade to no-op.
  Dictation must never be blocked by this feature.
- Per-session try/except during mute and restore: a session that vanished
  mid-dictation (app closed) must not prevent restoring the rest.
- COM is initialized per-call (`CoInitialize`/`CoUninitialize`) because the
  app calls these from short-lived worker threads.

### Config (`config.py`)

Add to `DEFAULTS["audio"]`: `"mute_during_dictation": True`.

### Integration (`app.py`)

- `App.__init__`: create `self.muter = SystemMuter()`.
- `_begin`: after `self.recorder.start()` succeeds and state is set to
  RECORDING, call `muter.mute_others()` if the config flag is on. (After —
  so a mic failure never leaves the system muted.)
- `_end`: after `self.recorder.stop()`, call `muter.restore()` on every
  path (including the quiet-tap early return).
- `_cancel`: call `muter.restore()`.
- `quit`/`_shutdown`: call `muter.restore()` as a shutdown step so no exit
  route leaves the system silenced.

### Dependency (`pyproject.toml`)

`"pycaw>=20240210; sys_platform == 'win32'"` (brings in `comtypes`).

## Out of scope (YAGNI)

- Chasing sessions that start playing mid-dictation (one-shot snapshot only).
- Ducking-percentage option.
- macOS/Linux implementations.
- Tray toggle UI (config-file flag only for now).

## Testing

- Unit tests (`tests/test_ducking.py`) with faked session objects: mute
  snapshots prior state, own PID excluded, restore puts states back exactly,
  restore is idempotent, a raising session doesn't break the others,
  non-Windows/no-pycaw is a no-op.
- Manual live check: play YouTube → hold Ctrl+Win → silence → release →
  audio returns; Esc cancel path; app quit while recording.
