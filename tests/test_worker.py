import db
import worker


def setup_tmp(tmp_path, monkeypatch, local=True):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "DESIGNS_DIR", str(tmp_path / "designs"))
    monkeypatch.setattr(worker.pipeline, "has_local", lambda: local)
    db.init()


def queue_one():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('dog dad', '', 'queued')")


def test_idle_without_queued_rows(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert worker.process_next() is False


def test_skips_when_no_gpu(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=False)
    queue_one()
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_generates_writes_file(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda prompt, on_step=None, size=None, decode_share=None: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["file"] == "designs/%d.png" % row["id"]


def test_reports_progress_via_callback(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def fake(prompt, on_step=None, size=None, decode_share=None):
        on_step(20)
        on_step(80)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["progress"] == 80


def test_failure_marks_failed_with_error(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def boom(prompt, on_step=None, size=None, decode_share=None):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker.pipeline, "generate_image_local", boom)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "failed"
    assert "model exploded" in row["error"]


def test_uses_stored_prompt_verbatim(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p, on_step=None, size=None, decode_share=None: seen.setdefault("prompt", p) or b"PNG")
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, prompt) "
                    "VALUES ('dog dad', 'vintage', 'queued', 'a neon dog wizard')")
    worker.process_next()
    assert seen["prompt"] == "a neon dog wizard"


def queue_poster():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, product) "
                    "VALUES ('a lighthouse', '', 'queued', 'poster')")


def test_poster_generates_at_the_poster_size(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}

    def fake(prompt, on_step=None, size=None, decode_share=None):
        seen.update(prompt=prompt, size=size, decode_share=decode_share)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    monkeypatch.setattr(worker.pipeline, "poster_size", lambda: (960, 1344))
    queue_poster()
    assert worker.process_next() is True
    assert seen["size"] == (960, 1344)
    assert seen["decode_share"] == 0.95
    assert "poster print" in seen["prompt"].lower()


def test_tee_still_generates_square_at_the_current_size(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}

    def fake(prompt, on_step=None, size=None, decode_share=None):
        seen.update(prompt=prompt, size=size)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    monkeypatch.setattr(worker.pipeline, "current_size", lambda: 1024)
    queue_one()
    assert worker.process_next() is True
    assert seen["size"] == (1024, 1024)
    assert "t-shirt" in seen["prompt"].lower()


def test_unknown_product_generates_as_a_tee(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p, on_step=None, size=None, decode_share=None:
                        seen.update(size=size) or b"PNG")
    monkeypatch.setattr(worker.pipeline, "current_size", lambda: 1024)
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, product) "
                    "VALUES ('dog dad', '', 'queued', 'hoodie')")
    assert worker.process_next() is True
    assert seen["size"] == (1024, 1024)


def test_test_row_still_uses_its_raw_prompt_whatever_the_product(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p, on_step=None, size=None, decode_share=None:
                        seen.update(prompt=p, size=size) or b"PNG")
    monkeypatch.setattr(worker.pipeline, "poster_size", lambda: (960, 1344))
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, test, product) "
                    "VALUES ('a red dragon', '', 'queued', 1, 'poster')")
    assert worker.process_next() is True
    assert seen["prompt"] == "a red dragon"   # no template, either product's
    assert seen["size"] == (960, 1344)        # but still the poster's shape
