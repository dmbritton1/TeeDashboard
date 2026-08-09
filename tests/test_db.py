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


def test_progress_column_added_with_default_zero(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        assert "progress" in cols
        con.execute("INSERT INTO designs (phrase) VALUES ('x')")
        row = con.execute("SELECT progress FROM designs").fetchone()
    assert row["progress"] == 0


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


def test_product_column_defaults_to_tee(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        assert "product" in cols
        # a row inserted by code that predates products must still land somewhere valid
        con.execute("INSERT INTO designs (phrase) VALUES ('dog dad')")
        row = con.execute("SELECT product FROM designs").fetchone()
    assert row["product"] == "tee"


def test_listing_columns_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"listing_title", "listing_tags", "listing_hook"} <= cols


def test_listing_tags_defaults_to_empty_string_not_null(tmp_path, monkeypatch):
    # printify splits this on "," - a NULL would be a TypeError at publish time
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase) VALUES ('x')")
        row = con.execute("SELECT listing_tags FROM designs").fetchone()
    assert row["listing_tags"] == ""


def test_listing_columns_are_added_to_a_pre_existing_table(tmp_path, monkeypatch):
    # the migration path real databases take, not the fresh-schema one
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with db.connect() as con:
        con.execute("CREATE TABLE designs (id INTEGER PRIMARY KEY, phrase TEXT NOT NULL)")
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"listing_title", "listing_tags", "listing_hook"} <= cols
