import os
import tempfile

import db

# point the app at a throwaway DB before main's import-time db.init() runs
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "api.db")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


def _reset():
    with db.connect() as con:
        con.execute("DELETE FROM designs")
        con.execute("DELETE FROM settings")


def test_generation_open_when_no_code_set():
    _reset()
    r = client.post("/api/test", json={"text": "a red dragon"})
    assert r.status_code == 200, r.text


def test_generation_blocked_without_code_header_when_code_set():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"})
    assert r.status_code == 401


def test_generation_blocked_with_wrong_code():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"},
                    headers={"X-Access-Code": "nope"})
    assert r.status_code == 401


def test_generation_allowed_with_correct_code():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"},
                    headers={"X-Access-Code": "hunter2"})
    assert r.status_code == 200, r.text


def test_reading_designs_never_gated():
    _reset()
    db.set_setting("access_code", "hunter2")
    assert client.get("/api/designs").status_code == 200
    assert client.get("/api/status").status_code == 200


def test_settings_open_when_no_code_set():
    _reset()
    r = client.post("/api/settings", json={"access_code": "hunter2"})
    assert r.status_code == 200, r.text
    assert client.get("/api/status").json()["access_code"] is True


def test_settings_gated_once_code_set():
    _reset()
    db.set_setting("access_code", "hunter2")
    # no header -> cannot overwrite the code
    assert client.post("/api/settings", json={"access_code": "attacker"}).status_code == 401
    # correct header -> owner can still change settings
    r = client.post("/api/settings", json={"gemini_api_key": "k"},
                    headers={"X-Access-Code": "hunter2"})
    assert r.status_code == 200, r.text


import worker  # noqa: E402


def test_queue_cap_rejects_when_full():
    _reset()
    with db.connect() as con:
        for _ in range(worker.MAX_QUEUE):
            con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
    r = client.post("/api/test", json={"text": "one too many"})
    assert r.status_code == 429


def test_queue_cap_allows_below_limit():
    _reset()
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
    r = client.post("/api/test", json={"text": "still room"})
    assert r.status_code == 200, r.text


def test_all_mutating_design_actions_gated_when_code_set():
    _reset()
    db.set_setting("access_code", "hunter2")
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','pending')")
        did = con.execute("SELECT id FROM designs").fetchone()["id"]
    for action in ["approve", "reject", "retry", "regenerate", "publish", "unreview"]:
        r = client.post(f"/api/designs/{did}/{action}")
        assert r.status_code == 401, f"{action} not gated (got {r.status_code})"
    # DELETE method is also gated
    r = client.delete(f"/api/designs/{did}")
    assert r.status_code == 401, f"DELETE not gated (got {r.status_code})"


def test_regenerate_respects_queue_cap():
    _reset()
    with db.connect() as con:
        for _ in range(worker.MAX_QUEUE):
            con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('src','','pending')")
        did = con.execute("SELECT id FROM designs WHERE status='pending'").fetchone()["id"]
    r = client.post(f"/api/designs/{did}/regenerate")
    assert r.status_code == 429


def test_queue_cap_rejects_generate_when_full():
    _reset()
    with db.connect() as con:
        for _ in range(worker.MAX_QUEUE):
            con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
    r = client.post("/api/generate", json={"text": "funny shirt"})
    assert r.status_code == 429
