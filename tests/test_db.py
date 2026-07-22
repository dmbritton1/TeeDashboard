import db


def setup_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()


def test_init_is_idempotent(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # second call must not raise


def test_usage_counter(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert db.images_today() == 0
    db.record_image()
    db.record_image()
    assert db.images_today() == 2


def test_migrations_add_columns_idempotently(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"tags", "rating", "product_id", "reviewed_at"} <= cols


def test_settings_roundtrip_and_env_fallback(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert db.get_setting("gemini_api_key") is None
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert db.get_setting("gemini_api_key") == "env-key"
    db.set_setting("gemini_api_key", "db-key")
    assert db.get_setting("gemini_api_key") == "db-key"


def test_prompt_column_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert "prompt" in cols
