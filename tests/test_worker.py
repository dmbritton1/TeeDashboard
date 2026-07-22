import db
import worker


def setup_tmp(tmp_path, monkeypatch, local=False):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "DESIGNS_DIR", str(tmp_path / "designs"))
    monkeypatch.setattr(worker.pipeline, "has_local", lambda: local)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.init()


def queue_one():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('dog dad', '', 'queued')")


def test_idle_without_queued_rows(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    assert worker.process_next() is False


def test_waits_without_api_key(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    queue_one()
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_respects_daily_cap(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    queue_one()
    monkeypatch.setattr(db, "images_today", lambda: worker.DAILY_CAP)
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_generates_writes_file_and_counts(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    monkeypatch.setattr(worker.pipeline, "generate_image", lambda prompt, key: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["file"] == "designs/%d.png" % row["id"]
    assert db.images_today() == 1


def test_local_gpu_needs_no_key_and_skips_cap(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    monkeypatch.setattr(worker.pipeline, "generate_image_local", lambda prompt, on_step=None: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert db.images_today() == 0  # local generations don't consume the Gemini cap


def test_reports_progress_via_callback(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def fake(prompt, on_step=None):
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
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")

    def boom(prompt, key):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker.pipeline, "generate_image", boom)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "failed"
    assert "model exploded" in row["error"]
