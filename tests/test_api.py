"""Endpoint tests by direct function call (no HTTP client needed)."""
import importlib
import zipfile

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


def test_delete_finished_test_image_bypasses_status_guard(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending", test=1)  # a generated scratch image lands in 'pending'
    main.delete_design(did)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM designs").fetchone()["c"] == 0


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


def test_settings_roundtrips_gemini_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["gemini_api_key"] is False
    main.save_settings(main.SettingsBody(gemini_api_key="secret-key"))
    assert main.get_settings()["gemini_api_key"] is True   # reported as a bool, never echoed
    assert db.get_setting("gemini_api_key") == "secret-key"


def test_test_gemini_no_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.test_gemini() == {"ok": False, "message": "No Gemini key saved yet"}


def test_test_gemini_ok(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "k")
    monkeypatch.setattr(main.requests, "get", lambda *a, **kw: FakeResp(200))
    assert main.test_gemini()["ok"] is True


def test_unreview_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("queued")
    with pytest.raises(HTTPException) as e:
        main.unreview(did)
    assert e.value.status_code == 409


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or []
        self.text = text

    def json(self):
        return self._payload


def test_export_csv(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    insert("published", tags="funny", rating=4, product_id="p1")
    resp = main.export_csv()
    body = resp.body.decode()
    lines = body.strip().splitlines()
    assert lines[0] == "id,phrase,style,status,tags,rating,product_id,created_at"
    assert "dog dad" in lines[1] and "p1" in lines[1]


def test_backup_zip_contains_db_and_images(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "1.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    resp = main.backup()
    with zipfile.ZipFile(resp.path) as z:
        names = z.namelist()
    assert "designs.db" in names and "designs/1.png" in names


def test_test_printify_wrong_shop(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "42")
    monkeypatch.setattr(main.requests, "get",
                        lambda *a, **kw: FakeResp(200, payload=[{"id": 7, "title": "Other"}]))
    out = main.test_printify()
    assert out["ok"] is False and "42" in out["message"]


import refine


def test_generate_stores_refined_prompts(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    monkeypatch.setattr(refine, "refine", lambda ph, fi, n, sp: ["prompt A", "prompt B"][:n])
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=2, refine=True))
    assert res == {"queued": 2, "refined": True}
    with db.connect() as con:
        prompts = [r["prompt"] for r in con.execute("SELECT prompt FROM designs ORDER BY id")]
    assert prompts == ["prompt A", "prompt B"]


def test_generate_falls_back_when_gemma_fails(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("no key")
    monkeypatch.setattr(refine, "refine", boom)
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=2, refine=True))
    assert res == {"queued": 2, "refined": False}
    with db.connect() as con:
        prompts = [r["prompt"] for r in con.execute("SELECT prompt FROM designs")]
    assert prompts == [None, None]


def test_generate_refine_off_skips_gemma(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    monkeypatch.setattr(refine, "refine", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=1, refine=False))
    assert res == {"queued": 1, "refined": False}


def test_settings_returns_refine_prompt_default(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["refine_prompt"] == refine.DEFAULT_REFINE_PROMPT
