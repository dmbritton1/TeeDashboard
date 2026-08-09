import os
import tempfile

import db
import worker

# point the app at a throwaway DB before main's import-time db.init() runs
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "api.db")
# ...and stop main's import-time worker.start() from spawning a real generation
# thread. The queue-cap tests below insert 'queued' rows, which a live worker
# picks up and tries to render - loading the whole image model into RAM and
# getting the test process OOM-killed.
worker.start = lambda: None

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)
client.cookies.set("auth", main.COOKIE)


def _reset():
    with db.connect() as con:
        con.execute("DELETE FROM designs")
        con.execute("DELETE FROM settings")


def _anon():
    """A client with no cookie. Fresh each time: TestClient keeps cookies."""
    return TestClient(main.app)


def test_api_refused_without_cookie():
    assert _anon().get("/api/designs").status_code == 401


def test_dashboard_redirects_to_login_without_cookie():
    r = _anon().get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_design_images_refused_without_cookie():
    # the /designs mount was the real hole: a leaked link showed every image
    r = _anon().get("/designs/anything.png", follow_redirects=False)
    assert r.status_code == 303


def test_login_page_and_its_stylesheet_are_reachable():
    a = _anon()
    assert a.get("/login").status_code == 200
    assert a.get("/static/styles.css").status_code == 200


def test_correct_password_sets_cookie_and_opens_the_door():
    a = _anon()
    r = a.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert a.cookies.get("auth")
    assert a.get("/api/designs").status_code == 200


def test_wrong_password_sets_no_cookie():
    a = _anon()
    r = a.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Incorrect" in r.text
    assert not a.cookies.get("auth")


def test_logout_clears_the_cookie():
    a = _anon()
    a.post("/login", data={"password": "test-password"})
    a.post("/logout", follow_redirects=False)
    assert a.get("/api/designs").status_code == 401


def test_data_exports_need_a_cookie():
    _reset()
    a = _anon()
    for path in ("/api/export.csv", "/api/backup"):
        assert a.get(path).status_code == 401, f"{path} not gated"
        assert client.get(path).status_code == 200, f"{path}: refused a signed-in client"


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
