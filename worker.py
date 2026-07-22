"""Background queue worker: generates queued designs on the local GPU with FLUX."""
import os
import threading
import time

import db
import pipeline

MAX_QUEUE = 30  # bound in-flight images so a shared link can't flood the GPU
DESIGNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "designs")


def process_next() -> bool:
    """Generate one queued design on the GPU. Returns True if work was attempted."""
    if not pipeline.has_local():
        return False  # no GPU here — leave designs queued instead of failing them
    with db.connect() as con:
        row = con.execute(
            # test images jump ahead so a scratch prompt isn't stuck behind a big batch
            "SELECT * FROM designs WHERE status = 'queued' ORDER BY test DESC, id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE designs SET status = 'generating' WHERE id = ?", (row["id"],))
    try:
        # A refined Gemma prompt (or a raw Test-tab prompt) is used verbatim;
        # otherwise wrap the phrase in the t-shirt template.
        prompt = row["prompt"] or (row["phrase"] if row["test"] else pipeline.build_prompt(row["phrase"], row["filters"]))
        png = pipeline.generate_image_local(prompt)
        os.makedirs(DESIGNS_DIR, exist_ok=True)
        with open(os.path.join(DESIGNS_DIR, "%d.png" % row["id"]), "wb") as f:
            f.write(png)
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'pending', file = ?, error = NULL WHERE id = ?",
                ("designs/%d.png" % row["id"], row["id"]),
            )
    except Exception as e:
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'failed', error = ? WHERE id = ?",
                (str(e)[:500], row["id"]),
            )
    return True


def run() -> None:
    while True:
        try:
            worked = process_next()
        except Exception:
            worked = False  # never let the worker thread die
        time.sleep(2)  # local GPU can go back-to-back; short idle poll when nothing queued


def start() -> None:
    threading.Thread(target=run, daemon=True).start()
