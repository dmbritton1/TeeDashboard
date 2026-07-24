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
