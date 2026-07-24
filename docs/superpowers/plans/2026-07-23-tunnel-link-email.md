# Tunnel Link Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command on the GPU machine starts the dashboard behind a Cloudflare Quick Tunnel and emails the session's random URL to the owner.

**Architecture:** A single supervisor script, `share.py`, at the repo root. It spawns uvicorn and cloudflared as child processes, watches cloudflared's output for the `trycloudflare.com` URL, and mails it once via `smtplib`. Nothing in the app itself changes — `share.py` is never imported by `main.py`, `worker.py`, or the dashboard.

**Tech Stack:** Python 3.12, standard library only (`subprocess`, `re`, `smtplib`, `email.message`) plus `python-dotenv`, which is already a dependency.

## Global Constraints

- No new entries in `requirements.txt` or `requirements-local.txt`. `smtplib` and `email` are stdlib; `python-dotenv` is already installed.
- Configuration comes from three `.env` variables, verbatim: `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`. `.env` is already gitignored — never commit it, never add real credentials to any tracked file.
- Never let a notification failure stop the tunnel. `send_link` must not raise.
- The tunnel origin is `http://127.0.0.1:8000`, never `localhost` — on Windows `localhost` resolves to `::1` first and uvicorn listens on IPv4 only.
- Match existing codebase style: `%`-style string formatting (see `main.py:270`), plain pytest with no fixtures framework (see `tests/test_access.py`).
- Mark deliberate simplifications with a `ponytail:` comment.

## File Structure

| File | Responsibility |
|---|---|
| `share.py` (create) | Whole feature: URL extraction, email send, process supervision. ~60 lines. |
| `tests/test_share.py` (create) | Unit tests for the two pure-ish functions. |
| `README.md` (modify, lines 79–108) | Replace the two-terminal share instructions with the one-command version. |

`share.py` stays one file. Splitting a 60-line script into a notifier module plus a supervisor module would add an import boundary that buys nothing.

---

### Task 1: URL extraction and email delivery

**Files:**
- Create: `share.py`
- Test: `tests/test_share.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `TUNNEL_RE: re.Pattern` — compiled pattern for the quick-tunnel URL.
  - `extract_url(line: str) -> str | None` — the matched URL, or `None`.
  - `send_link(url: str) -> None` — sends one email; never raises.
  - `PORT: int` — `8000`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_share.py`:

```python
"""Unit tests for the tunnel-link notifier. No subprocesses, no live Gmail."""
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_share.py -v`

Expected: collection error — `ModuleNotFoundError: No module named 'share'`.

- [ ] **Step 3: Write the minimal implementation**

Create `share.py`:

```python
"""Start the dashboard behind a Cloudflare Quick Tunnel and email the link.

Run this instead of starting uvicorn and cloudflared in separate terminals:

    .venv\\Scripts\\python share.py
"""
import os
import re
import smtplib
import subprocess
import sys
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

PORT = 8000
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def extract_url(line):
    """The trycloudflare URL in a line of cloudflared output, or None."""
    match = TUNNEL_RE.search(line)
    return match.group(0) if match else None


def send_link(url):
    """Email the tunnel URL. Never raises - a failed send must not stop the tunnel."""
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("NOTIFY_EMAIL")
    missing = [
        name
        for name, value in (
            ("GMAIL_USER", user),
            ("GMAIL_APP_PASSWORD", password),
            ("NOTIFY_EMAIL", to),
        )
        if not value
    ]
    if missing:
        print("no email sent - set %s in .env" % ", ".join(missing))
        return

    msg = EmailMessage()
    msg["Subject"] = "Dashboard is up: %s" % url
    msg["From"] = user
    msg["To"] = to
    msg.set_content(url)
    try:
        # Google displays app passwords in four space-separated groups
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(user, password.replace(" ", ""))
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print(
            "Gmail rejected the login - check GMAIL_APP_PASSWORD and that "
            "2-Step Verification is on for %s" % user
        )
    except Exception as e:  # ponytail: any send failure, the tunnel keeps running
        print("email failed (%s) - the link above still works" % e)
    else:
        print("emailed the link to %s" % to)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_share.py -v`

Expected: 6 passed.

- [ ] **Step 5: Confirm the rest of the suite is still green**

Run: `.venv/bin/pytest -q`

Expected: all tests pass, no new failures.

- [ ] **Step 6: Commit**

```bash
git add share.py tests/test_share.py
git commit -m "feat: email the cloudflare tunnel link when the site comes up"
```

---

### Task 2: Process supervision and operator docs

**Files:**
- Modify: `share.py` (append `main()` and the `__main__` guard)
- Modify: `README.md:79-108`

**Interfaces:**
- Consumes: `extract_url(line) -> str | None`, `send_link(url) -> None`, `PORT` from Task 1.
- Produces: `main() -> None`, invoked by the `if __name__ == "__main__"` guard.

There is no unit test for this task. It is subprocess orchestration — mocking `Popen` would test the mock, not the behavior. Step 4 is a real manual run instead.

- [ ] **Step 1: Append the supervisor to `share.py`**

Add to the end of `share.py`, after `send_link`:

```python
def main():
    """Run uvicorn and cloudflared together; email the first tunnel URL seen."""
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", str(PORT)]
    )
    tunnel = None
    try:
        # 127.0.0.1, not localhost: on Windows localhost resolves to ::1 first
        # and uvicorn listens on IPv4 only, so cloudflared gets connection refused
        tunnel = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://127.0.0.1:%d" % PORT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        sent = False
        for line in tunnel.stdout:
            print(line, end="")
            if not sent:
                url = extract_url(line)
                if url:
                    sent = True  # cloudflared echoes the URL more than once
                    send_link(url)
    except FileNotFoundError:
        print(
            "cloudflared not found - install it from https://developers."
            "cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        )
    except KeyboardInterrupt:
        pass
    finally:
        for process in (tunnel, server):
            if process:
                process.terminate()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the module still imports cleanly and tests pass**

Run: `.venv/bin/pytest tests/test_share.py -q`

Expected: 6 passed. Importing `share` must not spawn anything — if any subprocess starts during collection, the `__main__` guard is wrong.

- [ ] **Step 3: Verify the supervisor starts and stops cleanly**

Run: `.venv/bin/python share.py`

Expected within ~15 seconds:
- uvicorn's startup lines ("Uvicorn running on http://0.0.0.0:8000")
- cloudflared's banner containing a `https://<random>.trycloudflare.com` URL
- one line reading `emailed the link to <NOTIFY_EMAIL>` (or, if `.env` is not filled in, `no email sent - set ... in .env`)

Then press Ctrl-C. Both processes must exit. Confirm nothing is left listening:

```bash
lsof -i :8000
```

Expected: no output.

If `cloudflared` is not installed on this Mac, the expected result is the install message and a clean exit — that is a valid pass for this step, and the rest is verified on the GPU machine.

- [ ] **Step 4: Confirm the emailed link actually loads**

Open the URL from the email in a browser. Expected: the dashboard loads. This is the only end-to-end proof that the URL was scraped correctly rather than merely matched.

- [ ] **Step 5: Replace the share section in `README.md`**

Replace lines 79-108 (the `## Share with a few people (any device)` section, through the "Notes:" paragraph) with:

```markdown
## Share with a few people (any device)

The dashboard runs on this machine; a tunnel gives it a public link so others
can open it in a browser and queue images. Generation still happens locally.

1. Set an **Access code** in the dashboard settings (gates image generation; a
   leaked link alone can't queue work). Without a code the link is open.
2. Install `cloudflared` from Cloudflare's site.
3. Run:

       .venv\Scripts\python share.py

That starts the server, opens the tunnel, and prints a
`https://<random>.trycloudflare.com` URL. Share it. Anyone who opens it gets the
dashboard and, on first generate, is asked for the access code. Ctrl-C stops
both the server and the tunnel.

### Email the link automatically

`share.py` can mail the URL to whoever needs it, since the address changes every
restart. Add three lines to `.env` on this machine:

    GMAIL_USER=throwaway@gmail.com
    GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
    NOTIFY_EMAIL=where-it-should-land@gmail.com

`GMAIL_USER` is the account that sends; `NOTIFY_EMAIL` is where it lands. They
are different accounts on purpose — the sending account's password sits in a
plaintext file, so use a throwaway, not an account you care about.

The app password is a 16-character key from the sending account's Google
settings (Security → App passwords). It only exists once **2-Step Verification
is turned on** for that account; without 2FA the option is not shown at all.
Google shows the password once, in four space-separated groups — paste it with
or without the spaces, both work.

Without these variables `share.py` runs normally and just prints the link.

Notes: the tunnel URL changes each time you restart. Generation is serialized on
one GPU, so images queue (~a few minutes each); the queue is capped at 30
in-flight to prevent flooding.
```

- [ ] **Step 6: Verify the README renders and the old two-terminal instructions are gone**

Run: `grep -n "cloudflared tunnel --url" README.md`

Expected: no output (the manual two-terminal invocation is fully replaced).

- [ ] **Step 7: Commit**

```bash
git add share.py README.md
git commit -m "feat: one-command share script, document the .env email setup"
```

---

## Done when

- `.venv/bin/pytest -q` is green.
- `python share.py` brings up the site, prints a tunnel URL, and delivers that URL by email.
- `README.md` documents the one-command flow and the app-password setup.
- No credentials appear in any tracked file (`git status` shows `.env` untracked).
