import os
import tempfile

import db
import printify
import worker

# point the app at a throwaway DB before main's import-time db.init() runs
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "api.db")
# ...and stop main's import-time worker.start() from spawning a real generation
# thread. The queue-cap tests below insert 'queued' rows, which a live worker
# picks up and tries to render - loading the whole image model into RAM and
# getting the test process OOM-killed.
worker.start = lambda: None
# ...and stop main's import-time printify.verify() thread from making a real
# call to Printify: db.get_setting falls back to os.environ, so a machine with
# PRINTIFY_API_TOKEN set satisfies verify()'s guard even against this empty DB.
# The thread binds its target at construction, so stubbing across main's import
# is enough - and it is put back below, because this rebinding is process-wide
# and pytest imports every test module into one process.
_real_verify = printify.verify
printify.verify = lambda: (False, "")

from fastapi.testclient import TestClient  # noqa: E402

try:
    import main  # noqa: E402
finally:
    # in a finally so a failed import cannot leave the stub installed for the
    # rest of the process - a later module calling printify.verify() would get
    # (False, "") and fail confusingly, hiding the real import error
    printify.verify = _real_verify

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


DATA_EXPORTS = ["/api/export.csv", "/api/backup"]


def test_data_exports_open_when_no_code_set():
    _reset()
    for path in DATA_EXPORTS:
        assert client.get(path).status_code == 200, path


def test_data_exports_gated_once_code_set():
    _reset()
    db.set_setting("access_code", "hunter2")
    # the backup zip contains designs.db, which holds the code and Printify token
    for path in DATA_EXPORTS:
        assert client.get(path).status_code == 401, f"{path} not gated"
        r = client.get(path, headers={"X-Access-Code": "hunter2"})
        assert r.status_code == 200, f"{path}: {r.text}"


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
    # PATCH routes the Etsy title/tags/hook, so it must be gated too
    r = client.patch(f"/api/designs/{did}", json={"rating": 3})
    assert r.status_code == 401, f"PATCH not gated (got {r.status_code})"


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
