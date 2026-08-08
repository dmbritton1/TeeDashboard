import subprocess
import sys

import pipeline
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


# main() walks the ladder. Every rung costs a model load and a failed one can cost
# the full 30-min timeout, so which sizes it decides to try - and which it skips -
# is the difference between a 20-minute answer and a 3-hour one.
SQUARE = (1024, 1024)
FLOOR = pipeline.POSTER_LADDER[-1]       # cheapest 5:7 size, the non-square control
TOP = pipeline.POSTER_LADDER[0]


def _walk(monkeypatch, tmp_path, working):
    """Run main() with the GPU faked out. `working` is the set of (w, h) sizes that
    generate successfully; every other size fails. Returns what it tried, in order,
    and the report it wrote."""
    tried = []

    def fake_try_size(width, height, timeout=probe_poster.LADDER_TIMEOUT):
        tried.append((width, height))
        if (width, height) in working:
            return True, "3.2 min"
        return False, "MIOpen Error: no kernel for this conv (after 4.1 min)"

    monkeypatch.setattr(probe_poster, "try_size", fake_try_size)
    monkeypatch.setattr(probe_poster, "OUT_DIR", str(tmp_path))
    probe_poster.main()
    return tried, (tmp_path / "RESULT.txt").read_text()


def test_main_stops_when_the_square_control_fails(monkeypatch, tmp_path):
    tried, report = _walk(monkeypatch, tmp_path, working=set())
    assert tried == [SQUARE]            # nothing else is worth a model load
    assert "setup problem" in report


def test_main_stops_when_the_smallest_non_square_fails(monkeypatch, tmp_path):
    # the entire point of the floor control: if 5:7 is broken, the six bigger rungs
    # cost hours and prove nothing
    tried, report = _walk(monkeypatch, tmp_path, working={SQUARE})
    assert tried == [SQUARE, FLOOR]
    assert "non-square generation itself" in report


def test_main_walks_from_the_top_down_once_the_floor_holds(monkeypatch, tmp_path):
    tried, report = _walk(monkeypatch, tmp_path, working={SQUARE, FLOOR, (1040, 1456)})
    assert tried == [SQUARE, FLOOR, (1440, 2016), (1280, 1792), (1120, 1568), (1040, 1456)]
    assert "BEST: generate at 1040x1456" in report


def test_main_stops_at_the_first_size_that_works(monkeypatch, tmp_path):
    tried, report = _walk(monkeypatch, tmp_path, working={SQUARE, FLOOR, TOP})
    assert tried == [SQUARE, FLOOR, TOP]     # no reason to try anything smaller
    assert "BEST: generate at 1440x2016" in report


def test_main_never_retests_the_floor_it_already_proved(monkeypatch, tmp_path):
    tried, _ = _walk(monkeypatch, tmp_path, working={SQUARE, FLOOR})
    assert tried.count(FLOOR) == 1


def test_main_calls_the_floor_a_dead_end_when_nothing_above_it_works(monkeypatch, tmp_path):
    # non-square generates fine, just never at a size worth printing
    _, report = _walk(monkeypatch, tmp_path, working={SQUARE, FLOOR})
    assert "only the floor rung works" in report
    assert str(probe_poster.SELLABLE_DPI) in report
    assert "BEST: generate at" not in report     # must not read as a sellable result
