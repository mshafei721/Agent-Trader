"""Anti-stampede guards added after the 2026-06-10 reboot incident (18 stacked supervisors)."""
import os

from goldtrader.healing.heartbeat import pid_alive
from goldtrader.healing.watchdog import _pid_is_python


def test_pid_alive_own_process():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_bogus():
    assert pid_alive(99999999) is False
    assert pid_alive(None) is False
    assert pid_alive(-5) is False
    assert pid_alive("junk") is False


def test_pid_is_python_own_process():
    assert _pid_is_python(os.getpid()) is True


def test_pid_is_python_rejects_nonexistent():
    assert _pid_is_python(99999999) is False
