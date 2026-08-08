import subprocess
import sys

import probe_poster


def test_try_size_passes_on_a_clean_exit(monkeypatch):
    monkeypatch.setattr(probe_poster.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "wrote it", ""))
    ok, detail = probe_poster.try_size(960, 1344)
    assert ok is True
    assert "min" in detail          # the caller wants to know how long it took


def test_try_size_surfaces_the_last_stderr_line_on_failure(monkeypatch):
    crash = "Traceback (most recent call last):\nMIOpen Error: no kernel for this conv"
    monkeypatch.setattr(probe_poster.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", crash))
    ok, detail = probe_poster.try_size(1440, 2016)
    assert ok is False
    assert "MIOpen Error: no kernel for this conv" in detail


def test_try_size_reports_a_hang_instead_of_waiting_forever(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(probe_poster.subprocess, "run", fake_run)
    ok, detail = probe_poster.try_size(1440, 2016)
    assert ok is False
    assert "hung" in detail


def test_try_size_explains_a_silent_crash(monkeypatch):
    # a GPU context fault can kill the process with no Python traceback at all
    monkeypatch.setattr(probe_poster.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, -1073741819, "", ""))
    ok, detail = probe_poster.try_size(1280, 1792)
    assert ok is False
    assert "-1073741819" in detail


def test_try_size_runs_each_rung_in_a_separate_process(monkeypatch):
    """The whole point of the harness: a GPU context fault on one rung must not
    poison the next one, so each size gets a fresh interpreter."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(probe_poster.subprocess, "run", fake_run)
    probe_poster.try_size(1040, 1456)
    assert seen["cmd"][0] == sys.executable
    assert seen["cmd"][-1] == "1040x1456"


def test_try_size_always_passes_a_timeout(monkeypatch):
    # without one, a wedged MIOpen call would stall the probe indefinitely
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(probe_poster.subprocess, "run", fake_run)
    probe_poster.try_size(800, 1120)
    assert seen["timeout"] == probe_poster.LADDER_TIMEOUT
