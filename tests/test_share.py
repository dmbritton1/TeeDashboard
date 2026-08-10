"""Unit tests for the tunnel-link notifier. No subprocesses, no live Gmail."""
import smtplib

import pytest

import share

# a real line from cloudflared's startup banner
BANNER = "2026-07-23T18:04:11Z INF |  https://tidy-mango-pledge-vs.trycloudflare.com  |"
NOISE = "2026-07-23T18:04:10Z INF Registered tunnel connection connIndex=0"

URL = "https://tidy-mango-pledge-vs.trycloudflare.com"


class StubSMTP:
    """Stands in for smtplib.SMTP_SSL so tests never touch the network."""

    def __init__(self):
        self.logins = []
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.logins.append((user, password))

    def send_message(self, msg):
        self.sent.append(msg)


def _configure(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "bot@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("NOTIFY_EMAIL", "owner@gmail.com")


def _unconfigure(monkeypatch):
    for key in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"):
        monkeypatch.delenv(key, raising=False)


def test_extract_url_finds_the_tunnel_url():
    assert share.extract_url(BANNER) == URL


def test_extract_url_ignores_unrelated_output():
    assert share.extract_url(NOISE) is None


def test_send_link_without_config_never_connects(monkeypatch):
    _unconfigure(monkeypatch)
    stub = StubSMTP()
    monkeypatch.setattr(share.smtplib, "SMTP_SSL", lambda *a, **k: stub)

    share.send_link(URL)

    assert stub.logins == []
    assert stub.sent == []


def test_send_link_strips_spaces_from_the_app_password(monkeypatch):
    _configure(monkeypatch)
    stub = StubSMTP()
    monkeypatch.setattr(share.smtplib, "SMTP_SSL", lambda *a, **k: stub)

    share.send_link(URL)

    assert stub.logins == [("bot@gmail.com", "abcdefghijklmnop")]


def test_send_link_puts_the_url_in_the_subject_and_body(monkeypatch):
    _configure(monkeypatch)
    stub = StubSMTP()
    monkeypatch.setattr(share.smtplib, "SMTP_SSL", lambda *a, **k: stub)

    share.send_link(URL)

    msg = stub.sent[0]
    assert msg["To"] == "owner@gmail.com"
    assert msg["From"] == "bot@gmail.com"
    assert URL in msg["Subject"]
    assert URL in msg.get_content()


def test_send_link_swallows_smtp_failures(monkeypatch):
    _configure(monkeypatch)

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(share.smtplib, "SMTP_SSL", boom)

    share.send_link(URL)  # must return normally, not raise


def test_send_link_swallows_bad_app_password(monkeypatch):
    _configure(monkeypatch)
    stub = StubSMTP()

    def bad_login(user, password):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    stub.login = bad_login
    monkeypatch.setattr(share.smtplib, "SMTP_SSL", lambda *a, **k: stub)

    share.send_link(URL)  # must return normally, not raise


def test_relay_sends_once_when_url_repeats():
    sent = []
    lines = [BANNER, NOISE, BANNER]

    share.relay(lines, sent.append)

    assert sent == [URL]


def test_relay_never_sends_without_a_url():
    sent = []
    lines = [NOISE, NOISE]

    share.relay(lines, sent.append)

    assert sent == []


class FakeProcess:
    """Stands in for subprocess.Popen's return value."""

    def __init__(self, poll_value=None, stdout=None):
        self._poll_value = poll_value
        self.stdout = stdout or []

    def poll(self):
        return self._poll_value

    def terminate(self):
        pass


def test_main_checks_the_password_before_spawning_anything(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)

    def boom(*a, **k):
        raise AssertionError("subprocess.Popen should not run without a password")

    monkeypatch.setattr(share.subprocess, "Popen", boom)

    with pytest.raises(SystemExit, match="DASHBOARD_PASSWORD"):
        share.main()


def test_main_skips_the_email_when_the_server_already_exited(monkeypatch):
    # password is set, but the server process reports itself already dead
    # (e.g. it crashed on startup) - the tunnel URL it fronts is worthless
    monkeypatch.setenv("DASHBOARD_PASSWORD", "whatever")
    procs = [FakeProcess(poll_value=1), FakeProcess(stdout=[BANNER])]
    monkeypatch.setattr(share.subprocess, "Popen", lambda *a, **k: procs.pop(0))
    sent = []
    monkeypatch.setattr(share, "send_link", sent.append)

    share.main()

    assert sent == []
