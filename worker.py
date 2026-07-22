"""Background queue worker: paces Gemini calls to stay inside the free tier."""
import os
import threading
import time

import db
import pipeline

DAILY_CAP = 450        # stop 50 short of the ~500/day free-tier cap
MAX_QUEUE = 30         # bound in-flight images so a shared link can't flood the GPU
SECONDS_BETWEEN = 31   # ~2 images/min free-tier pace
DESIGNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "designs")


def process_next() -> bool:
    """Generate one queued design. Returns True if work was attempted."""
    local = pipeline.has_local()
    key = db.get_setting("gemini_api_key")
    if not local and (not key or db.images_today() >= DAILY_CAP):
        return False
    with db.connect() as con:
        row = con.execute(
            # test images jump ahead so a scratch prompt isn't stuck behind a big batch
            "SELECT * FROM designs WHERE status = 'queued' ORDER BY test DESC, id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        con.execute(
            "UPDATE designs SET status = 'generating', progress = 0 WHERE id = ?", (row["id"],)
        )
    try:
        # Test tab sends its text raw; the pipeline wraps its phrase in the t-shirt template
        prompt = row["phrase"] if row["test"] else pipeline.build_prompt(row["phrase"], row["filters"])

        def on_step(pct):
            with db.connect() as con:
                con.execute("UPDATE designs SET progress = ? WHERE id = ?", (pct, row["id"]))

        png = (
            pipeline.generate_image_local(prompt, on_step=on_step)
            if local
            else pipeline.generate_image(prompt, key)  # Gemini has no per-step callback
        )
        os.makedirs(DESIGNS_DIR, exist_ok=True)
        with open(os.path.join(DESIGNS_DIR, "%d.png" % row["id"]), "wb") as f:
            f.write(png)
        if not local:
            db.record_image()  # daily cap only meters Gemini free-tier calls
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'pending', file = ?, error = NULL WHERE id = ?",
                ("designs/%d.png" % row["id"], row["id"]),
            )
    except Exception as e:
        msg = str(e)[:500]
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            # rate-limited despite pacing: requeue and back off, never fail the item
            with db.connect() as con:
                con.execute("UPDATE designs SET status = 'queued' WHERE id = ?", (row["id"],))
            time.sleep(60)
        else:
            with db.connect() as con:
                con.execute(
                    "UPDATE designs SET status = 'failed', error = ? WHERE id = ?",
                    (msg, row["id"]),
                )
    return True


def run() -> None:
    while True:
        try:
            worked = process_next()
        except Exception:
            worked = False  # never let the worker thread die
        # Gemini free tier needs 31s pacing; a local GPU can go back-to-back
        time.sleep(SECONDS_BETWEEN if worked and not pipeline.has_local() else 2)


def start() -> None:
    threading.Thread(target=run, daemon=True).start()
