"""Endpoint tests by direct function call (no HTTP client needed)."""
import importlib

import pytest
from fastapi import HTTPException

import db
import worker


def load_main(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "start", lambda: None)
    import main
    main = importlib.reload(main)
    monkeypatch.setattr(main, "BASE", str(tmp_path))
    return main


def insert(status="pending", **kw):
    row = {"phrase": "dog dad", "filters": "vintage", "status": status, **kw}
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO designs (%s) VALUES (%s)"
            % (", ".join(row), ", ".join("?" * len(row))),
            tuple(row.values()),
        )
        return cur.lastrowid


def test_approve_sets_reviewed_at(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.approve(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "approved" and row["reviewed_at"]


def test_unreview_returns_to_pending(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("rejected", reviewed_at="2026-07-01 00:00:00")
    main.unreview(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "pending" and row["reviewed_at"] is None


def test_patch_tags_and_rating(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.patch_design(did, main.PatchBody(tags="funny, dog", rating=9))
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["tags"] == "funny, dog"
    assert row["rating"] == 5  # clamped


def test_patch_missing_design_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.patch_design(999, main.PatchBody(rating=3))
    assert e.value.status_code == 404


def test_patch_empty_body_400(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    with pytest.raises(HTTPException) as e:
        main.patch_design(did, main.PatchBody())
    assert e.value.status_code == 400


def test_delete_rejected_removes_row_and_files(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "9.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    did = insert("rejected", file="designs/9.png")
    main.delete_design(did)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM designs").fetchone()["c"] == 0
    assert not img.exists()


def test_delete_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    with pytest.raises(HTTPException) as e:
        main.delete_design(did)
    assert e.value.status_code == 409


def test_delete_missing_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.delete_design(1)
    assert e.value.status_code == 404


def test_publish_stores_product_id(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "s")
    pf = tmp_path / "designs" / "7-print.png"
    pf.parent.mkdir(exist_ok=True)
    pf.write_bytes(b"png")
    did = insert("approved", file="designs/7-print.png", print_file="designs/7-print.png")
    monkeypatch.setattr(main.printify, "publish", lambda row: "prod-123")
    main.publish(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "published" and row["product_id"] == "prod-123"


def test_settings_roundtrips_prompt_template(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.get_settings()
    assert out["prompt_template"] == main.DEFAULT_PROMPT
    main.save_settings(main.SettingsBody(prompt_template="my prompt"))
    assert main.get_settings()["prompt_template"] == "my prompt"


def test_unreview_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("queued")
    with pytest.raises(HTTPException) as e:
        main.unreview(did)
    assert e.value.status_code == 409
