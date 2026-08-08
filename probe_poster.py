"""Find the largest 50x70cm poster this GPU can actually generate.

Proves the square size still works, then proves the cheapest 5:7 size works, then
walks pipeline.POSTER_LADDER from best quality downward and stops at the first size
that works. The two controls come first because they fail differently: a broken
square is a setup problem, and a broken floor rung means non-square generation
itself is broken - in which case searching for a ceiling is hours wasted on an
answer that doesn't exist.

Each rung runs in a FRESH SUBPROCESS, and that isolation is the whole point: on
gfx103x (RX 6700) a MIOpen kernel fault kills the GPU context, so a ladder walked
inside one process would fail every rung after the first fault and report a floor
that isn't real.

    .venv\\Scripts\\python probe_poster.py             walk the ladder (Windows)
    .venv/bin/python probe_poster.py                  walk the ladder (Linux/macOS)
    .venv\\Scripts\\python probe_poster.py 1040x1456   one size (used internally)
"""
import os
import subprocess
import sys
import time

import pipeline

LADDER_TIMEOUT = 1800   # 30 min/rung - ~5x the measured 1024 time, so slow != hung
WARMUP_TIMEOUT = 7200   # 2h - the very first run may download ~15GB of weights
SELLABLE_DPI = 150      # normal floor for large wall art; under this is diagnostic only
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe")
PROMPT = (
    "Art deco travel poster, geometric symmetrical ornamental structure, bold "
    "limited palette, full-bleed artwork, no frame, no border, no mockup, no text"
)


def _child(width: int, height: int) -> None:
    """Generate one image at this size and save it. Runs in its own process."""
    import db

    db.init()  # current_model() reads a setting; the table may not exist yet
    png = pipeline.generate_image_local(PROMPT, size=(width, height))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "%dx%d.png" % (width, height))
    with open(path, "wb") as f:
        f.write(png)
    print("wrote %s" % path)


def try_size(width: int, height: int, timeout: int = LADDER_TIMEOUT) -> tuple[bool, str]:
    """Run one size in a fresh process so a GPU fault can't poison the next rung.
    Returns (ok, human-readable detail)."""
    started = time.time()
    try:
        done = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "%dx%d" % (width, height)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "hung - nothing back after %d min" % (timeout // 60)
    took = "%.1f min" % ((time.time() - started) / 60)
    if done.returncode == 0:
        return True, took
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    # a GPU context fault can kill the process outright, leaving no traceback to quote
    reason = lines[-1] if lines else "died with exit code %d and no output" % done.returncode
    return False, "%s (after %s)" % (reason[:200], took)


def _write_report(lines: list[str]) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "RESULT.txt")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\nSaved to %s - send that file back." % path)


def main() -> None:
    report: list[str] = []

    def say(line: str = "") -> None:
        print(line)
        report.append(line)

    say("Control: 1024x1024 square - the size that already works today.")
    say("If the weights aren't cached yet this downloads ~15GB first. Be patient.")
    ok, detail = try_size(1024, 1024, timeout=WARMUP_TIMEOUT)
    say("  1024x1024  %s  %s" % ("PASS" if ok else "FAIL", detail))
    if not ok:
        say()
        say("The control failed, so every rung below it would fail too and prove nothing.")
        say("This is a setup problem, not a poster problem. Fix it and re-run.")
        _write_report(report)
        return

    # Second control, and the cheapest run on the ladder: the floor rung has fewer
    # pixels than the square above it and a latent under tile_latent_min_size, so its
    # VAE decode is a smaller conv than the one that already works today. It separates
    # "non-square is broken" from "big is broken" for the price of a single rung -
    # and only the second of those has a ceiling worth searching for.
    say()
    floor = pipeline.POSTER_LADDER[-1]
    say("Non-square control: %dx%d - the cheapest 5:7 size on the ladder." % floor)
    ok, detail = try_size(*floor)
    say("  %dx%d  %s  %s" % (floor[0], floor[1], "PASS" if ok else "FAIL", detail))
    if not ok:
        say()
        say("The smallest 5:7 size failed while the square control passed, so the")
        say("problem is non-square generation itself, not how big it is. Every rung")
        say("above this one is larger and would fail the same way - walking them")
        say("would cost hours and prove nothing.")
        _write_report(report)
        return

    say()
    best = floor + (pipeline.poster_dpi(*floor),)   # the floor is proven, so it's the fallback
    for width, height in pipeline.POSTER_LADDER[:-1]:
        say("Trying %dx%d - %d dpi on a 50x70cm print" % (width, height, pipeline.poster_dpi(width, height)))
        ok, detail = try_size(width, height)
        say("  %s  %s" % ("PASS" if ok else "FAIL", detail))
        if ok:
            best = (width, height, pipeline.poster_dpi(width, height))
            break

    say()
    if best[2] < SELLABLE_DPI:
        say("BEST: only the floor rung works - %dx%d at %d dpi, under the %d dpi"
            % (best + (SELLABLE_DPI,)))
        say("large-format floor. Non-square generation works, just not at a size")
        say("worth selling. The ceiling is lower than the cheapest sellable poster.")
    else:
        say("BEST: generate at %dx%d for %d dpi on a 50x70cm print." % best)
    say("Now open probe/%dx%d.png and look at it. A size can finish without" % best[:2])
    say("crashing and still come out smeared, banded, or seamed by VAE tiling.")
    _write_report(report)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _child(*(int(n) for n in sys.argv[1].lower().split("x")))
    else:
        main()
