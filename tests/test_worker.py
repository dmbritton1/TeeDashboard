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
    monkeypatch.setattr(worker.pipeline, "generate_image_local", lambda prompt, on_step=None: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["file"] == "designs/%d.png" % row["id"]


def test_failure_marks_failed_with_error(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def boom(prompt, on_step=None):
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
                        lambda p, on_step=None: seen.setdefault("prompt", p) or b"PNG")
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, prompt) "
                    "VALUES ('dog dad', 'vintage', 'queued', 'a neon dog wizard')")
    worker.process_next()
    assert seen["prompt"] == "a neon dog wizard"
