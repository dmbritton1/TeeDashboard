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
