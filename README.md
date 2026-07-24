# T-Shirt Design Pipeline

Paste Etsy search phrases + style filters, generate design candidates with
Gemini (free tier), review them in a dashboard, approve the keepers
(auto-upscaled locally for print), and publish to Printify -> Etsy.

## Run it

    .venv/bin/uvicorn main:app --port 8000

Open http://localhost:8000

First-time setup (needs Python 3.10+; this repo's venv was built with uv):

    uv venv --seed --python 3.12 .venv
    .venv/bin/pip install -r requirements.txt

## Configure

Paste keys in the dashboard settings panel (stored in the local SQLite db):

- **Gemini API key** - from https://aistudio.google.com (required unless the
  machine has an NVIDIA GPU - see "Local generation" below)
- **Printify token + shop ID** - from Printify account settings once you
  have a Printify account connected to your Etsy shop (only needed to publish)

Environment variables `GEMINI_API_KEY`, `PRINTIFY_API_TOKEN`,
`PRINTIFY_SHOP_ID` (or a `.env` file) work as fallbacks.

## Input format

One design per line in the big textbox:

    funny fishing shirt | vintage, distressed, black shirt
    plant mom | retro 70s, floral, cream shirt
    dog dad

Left of `|` = the design concept. Right = optional comma-separated style
filters. 2 variations are generated per line.

## Local generation (machine with an NVIDIA GPU)

On a computer with an NVIDIA graphics card (8GB+ VRAM; 32GB+ system RAM
recommended on smaller cards), the app generates images locally with
FLUX.1-schnell instead of calling Gemini - no API key, no daily cap, no
per-image cost. Cards with 20GB+ VRAM run fast; smaller cards work but take
a few minutes per image. Setup on that machine:

    git clone https://github.com/dmbritton1/TeeDashboard.git
    cd TeeDashboard
    uv venv --seed --python 3.12 .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pip install -r requirements-local.txt

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\uvicorn` instead of
`.venv/bin/...`, and install the CUDA build of torch first:

    .venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu128

Then run the server as usual. The GPU is detected automatically (the status
bar shows "local GPU"), and the first generation downloads the model
(~20GB, one time). To use the dashboard from another computer, install
Tailscale (free) on both machines and open `http://<machine-name>:8000`.

Start the server so other computers can reach it:

    .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

## Rate limiting (built in)

Generation is paced at ~2 images/min and stops at 450 images/day to stay
inside Gemini's free tier. Big batches just take a while - paste the list,
walk away, come back to review. The status bar shows today's usage.

## Tests

    .venv/bin/pytest -q

## Share with a few people (any device)

The dashboard runs on this machine; a tunnel gives it a public link so others
can open it in a browser and queue images. Generation still happens locally.

1. Set an **Access code** in the dashboard settings. It gates image generation,
   review actions, settings, and the CSV export and backup download - so a
   leaked link alone can't queue work or pull your Printify token out of the
   backup zip. Anyone with the link can still *look* at designs. Without a code
   set, the link is open to everyone.
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
