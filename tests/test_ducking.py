"""Tests for the dictation system-audio muter (ducking.py).

Uses fake WASAPI-like session objects injected via get_sessions so no real
audio stack (pycaw/COM) is needed.
"""

import os

import pytest

from localflow.ducking import SystemMuter


class FakeVolume:
    def __init__(self, muted: bool = False):
        self.muted = muted
        self.set_calls = 0

    def GetMute(self) -> int:  # noqa: N802 - mirrors COM interface
        return int(self.muted)

    def SetMute(self, value, ctx) -> None:  # noqa: N802
        self.muted = bool(value)
        self.set_calls += 1


class FakeSession:
    def __init__(self, pid: int, muted: bool = False):
        self.ProcessId = pid
        self.SimpleAudioVolume = FakeVolume(muted)


def muter_with(sessions):
    return SystemMuter(get_sessions=lambda: sessions)


def test_mute_others_mutes_every_other_session():
    a, b = FakeSession(pid=100), FakeSession(pid=200)
    m = muter_with([a, b])
    m.mute_others()
    assert a.SimpleAudioVolume.muted
    assert b.SimpleAudioVolume.muted


def test_own_process_is_not_muted():
    me = FakeSession(pid=os.getpid())
    other = FakeSession(pid=100)
    m = muter_with([me, other])
    m.mute_others()
    assert not me.SimpleAudioVolume.muted
    assert me.SimpleAudioVolume.set_calls == 0
    assert other.SimpleAudioVolume.muted


def test_restore_puts_each_session_back_exactly():
    was_playing = FakeSession(pid=100, muted=False)
    was_muted = FakeSession(pid=200, muted=True)  # user muted it themselves
    m = muter_with([was_playing, was_muted])
    m.mute_others()
    assert was_playing.SimpleAudioVolume.muted
    assert was_muted.SimpleAudioVolume.muted
    m.restore()
    assert not was_playing.SimpleAudioVolume.muted
    assert was_muted.SimpleAudioVolume.muted  # stays muted, as the user had it


def test_restore_is_idempotent():
    s = FakeSession(pid=100)
    m = muter_with([s])
    m.mute_others()
    m.restore()
    calls_after_first = s.SimpleAudioVolume.set_calls
    m.restore()
    assert s.SimpleAudioVolume.set_calls == calls_after_first


def test_restore_without_mute_does_nothing():
    s = FakeSession(pid=100)
    m = muter_with([s])
    m.restore()
    assert s.SimpleAudioVolume.set_calls == 0


def test_mute_others_is_reentrant_and_keeps_original_snapshot():
    s = FakeSession(pid=100, muted=False)
    m = muter_with([s])
    m.mute_others()
    m.mute_others()  # second call must not snapshot the now-muted state
    m.restore()
    assert not s.SimpleAudioVolume.muted


def test_broken_session_does_not_block_the_rest():
    class BrokenVolume(FakeVolume):
        def SetMute(self, value, ctx):  # noqa: N802
            raise OSError("device removed")

    broken = FakeSession(pid=100)
    broken.SimpleAudioVolume = BrokenVolume()
    ok = FakeSession(pid=200)
    m = muter_with([broken, ok])
    m.mute_others()
    assert ok.SimpleAudioVolume.muted
    m.restore()
    assert not ok.SimpleAudioVolume.muted


def test_session_gone_by_restore_time_is_skipped():
    a, b = FakeSession(pid=100), FakeSession(pid=200)
    sessions = [a, b]
    m = SystemMuter(get_sessions=lambda: list(sessions))
    m.mute_others()
    sessions.remove(a)  # app closed mid-dictation
    m.restore()  # must not raise
    assert not b.SimpleAudioVolume.muted


def test_session_started_during_dictation_is_left_alone():
    a = FakeSession(pid=100)
    sessions = [a]
    m = SystemMuter(get_sessions=lambda: list(sessions))
    m.mute_others()
    newcomer = FakeSession(pid=300, muted=False)
    sessions.append(newcomer)
    m.restore()
    assert newcomer.SimpleAudioVolume.set_calls == 0


def test_failing_backend_is_a_noop():
    def boom():
        raise RuntimeError("COM unavailable")

    m = SystemMuter(get_sessions=boom)
    m.mute_others()  # must not raise
    m.restore()  # must not raise


def test_same_pid_twice_restores_each_session_state():
    tab_playing = FakeSession(pid=100, muted=False)
    tab_muted = FakeSession(pid=100, muted=True)
    m = muter_with([tab_playing, tab_muted])
    m.mute_others()
    m.restore()
    assert not tab_playing.SimpleAudioVolume.muted
    assert tab_muted.SimpleAudioVolume.muted
