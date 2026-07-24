# Email the tunnel link when the GPU machine starts the site

## Goal

When the GPU machine comes online, the owner gets an email containing that
session's `trycloudflare.com` URL — without anyone having to copy, paste, or
remember to send it.

Free Quick Tunnels mint a random URL on every restart (see
`2026-07-21-remote-access-design.md`). A stable URL was considered and rejected:
the tunnel stays disposable, and the address is delivered instead.

## What the operator runs

One command, replacing the current two-terminal routine:

    .venv\Scripts\python share.py

Ctrl-C stops the server and the tunnel together.

## Architecture

One new file, `share.py`, at the repo root. Nothing in `main.py`, `worker.py`,
or the dashboard changes. The script owns three responsibilities in sequence:

1. **Start the server** — `uvicorn main:app --host 0.0.0.0 --port 8000` as a
   child process, invoked via `sys.executable -m uvicorn` so it uses the same
   interpreter (and therefore the same venv) that launched the script.
2. **Start the tunnel** — `cloudflared tunnel --url http://127.0.0.1:8000`,
   with stderr merged into stdout and read line by line. `127.0.0.1` rather than
   `localhost`, because on Windows `localhost` resolves to `::1` first and
   uvicorn listens on IPv4 only.
3. **Notify on first URL** — the first line matching the tunnel URL pattern
   triggers one email, then relaying continues so the operator still sees
   cloudflared's own output.

The script is a supervisor, not a library. It is never imported by the app.

## Configuration

Three variables in `.env`, which is already gitignored and already loaded by
`python-dotenv` (an existing dependency, used by `main.py`):

    GMAIL_USER=<throwaway sending account>
    GMAIL_APP_PASSWORD=<16-char app password from that same account>
    NOTIFY_EMAIL=<destination inbox>

The sending account and the destination are deliberately separate. The sending
account's credentials live in a plaintext file on a machine the owner does not
control; a throwaway account bounds the damage to spam from an address nobody
cares about. The destination needs no credentials at all.

Gmail app passwords require 2-Step Verification on the sending account —
without it Google does not expose the feature. Plain-password SMTP is not an
option; Google removed it.

## Transport

`smtplib.SMTP_SSL("smtp.gmail.com", 465)` with an `email.message.EmailMessage`.
Both are standard library — no new dependency.

The URL goes in the subject line as well as the body, so it is readable from a
phone lock screen without opening the message.

Rejected alternatives: ntfy.sh (no credentials, but routes the link through a
third party and rate-limits anonymous email); a transactional email API
(another account, another key, no benefit over SMTP here).

## Interface

    TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    extract_url(line: str) -> str | None
        The matched URL, or None. Pure; the only piece with real logic.

    send_link(url: str) -> None
        Reads the three env vars. Missing any one → prints a warning and
        returns. Spaces are stripped from the app password, since Google
        displays it in four space-separated groups and it gets pasted that way.
        Never raises: every failure is caught and printed.

    main() -> None
        Spawns both processes, streams cloudflared's output, emails on the
        first URL seen, and in a finally block terminates whichever children
        were started (uvicorn is spawned first, so a cloudflared launch failure
        must still take the server down with it).

Only the first matching URL sends mail. cloudflared can echo the address more
than once per session; a single boolean prevents duplicate emails.

## Data flow

    share.py
      ├─ spawns uvicorn ──────────────▶ serves the dashboard on 0.0.0.0:8000
      └─ spawns cloudflared ──────────▶ public https://<random>.trycloudflare.com
             │
             stdout lines ─▶ extract_url ─▶ send_link ─▶ owner's inbox

## Error handling

The governing rule: **a failed notification never takes down the site.** The
URL is always printed locally, so the terminal is the fallback path.

| Condition | Behavior |
|---|---|
| `cloudflared` not on PATH | `FileNotFoundError` → message naming the download page, then normal shutdown |
| Any env var missing | Warning naming the missing variable; server and tunnel run normally |
| Gmail rejects the login | `SMTPAuthenticationError` → message pointing at the app password and 2FA; tunnel unaffected |
| Any other send failure | Warning printed; tunnel unaffected |
| Port 8000 already in use | uvicorn inherits the console, so its own error is visible |
| Ctrl-C | `finally` terminates both children |

## Testing

`tests/test_share.py`, plain pytest matching the existing suite:

- `extract_url` returns the URL from a captured cloudflared banner line.
- `extract_url` returns `None` for unrelated log output.
- `send_link` with the env vars unset returns without raising and without
  attempting a connection.
- `send_link` strips spaces from the app password before login (asserted
  against a stub SMTP object).

Process orchestration is not tested — no subprocess or live-Gmail tests.

## Out of scope

- Retrying a failed send.
- Re-emailing if cloudflared reconnects with a new URL mid-session.
- Auto-starting on Windows boot.
- A stable tunnel URL or custom domain (still deferred, as in the remote-access
  spec).
- Configuring any of this from the dashboard settings panel.
