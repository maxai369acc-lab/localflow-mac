"""Mute other apps' audio while dictating, restore it exactly afterwards.

Windows-only (WASAPI per-app sessions via pycaw). On other platforms, or if
pycaw/COM is unavailable, every method is a silent no-op — dictation must
never be blocked by this feature.

Playback (render) sessions only. Muting the *capture* side per-app is not
possible: ISimpleAudioVolume on a capture session controls the shared
endpoint, so muting another app's mic session silences every stream on the
device — ours included (verified live 2026-07-07: session and volume
changes on one capture session propagated to all sessions and the endpoint).

Session COM objects are never held across calls: mute_others() snapshots
(pid, was_muted) pairs and restore() re-enumerates sessions on the calling
thread, so short-lived worker threads can each do their own COM work.
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict, deque

log = logging.getLogger("localflow")

IS_WIN = sys.platform == "win32"


def _wasapi_sessions() -> list:
    """Enumerate live WASAPI audio sessions (Windows only)."""
    if not IS_WIN:
        return []
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    return [s for s in AudioUtilities.GetAllSessions() if s.SimpleAudioVolume]


class SystemMuter:
    """Mute every audio session except our own, then put it all back."""

    def __init__(self, get_sessions=None):
        self._get_sessions = get_sessions or _wasapi_sessions
        self._snapshot: list[tuple[int, bool]] | None = None
        self._warned = False

    def _sessions(self) -> list:
        try:
            return self._get_sessions()
        except Exception as e:
            if not self._warned:
                self._warned = True
                log.warning("audio session control unavailable: %s", e)
            return []

    def mute_others(self) -> None:
        if self._snapshot is not None:  # already muted (re-entrant)
            return
        snapshot: list[tuple[int, bool]] = []
        own_pid = os.getpid()
        for s in self._sessions():
            try:
                pid = int(s.ProcessId or 0)
                if pid == own_pid:
                    continue
                vol = s.SimpleAudioVolume
                snapshot.append((pid, bool(vol.GetMute())))
                vol.SetMute(1, None)
            except Exception as e:
                log.debug("could not mute session pid=%s: %s", getattr(s, "ProcessId", "?"), e)
        self._snapshot = snapshot

    def restore(self) -> None:
        if self._snapshot is None:
            return
        states: dict[int, deque[bool]] = defaultdict(deque)
        for pid, muted in self._snapshot:
            states[pid].append(muted)
        self._snapshot = None
        for s in self._sessions():
            try:
                pid = int(s.ProcessId or 0)
                if not states[pid]:  # unknown/new session: leave it alone
                    continue
                s.SimpleAudioVolume.SetMute(int(states[pid].popleft()), None)
            except Exception as e:
                log.debug("could not restore session pid=%s: %s", getattr(s, "ProcessId", "?"), e)
