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

## Local generation (machine with a GPU)

On a computer with a CUDA or ROCm GPU (10GB+ VRAM; 32GB+ system RAM
recommended), the app generates images locally with Z-Image-Turbo - no API
key, no daily cap, no per-image cost. Fast cards finish in seconds; smaller
ones take a few minutes per image. Setup on that machine:

    git clone https://github.com/dmbritton1/TeeDashboard.git
    cd TeeDashboard
    uv venv --seed --python 3.12 .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/pip install -r requirements-local.txt

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\uvicorn` instead of
`.venv/bin/...`, and install a GPU build of torch first. NVIDIA:

    .venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu128

AMD (ROCm on Windows, e.g. an RX 6700 - install torchvision from the same
index or torchvision ops will fail to resolve against this torch):

    .venv\Scripts\pip install torch torchvision --index-url https://rocm.nightlies.amd.com/v2-staging/gfx103X-dgpu/

Then run the server as usual. The GPU is detected automatically (the status
bar shows "local GPU"), and the first generation downloads the model
(~15GB, one time: a 7.2GB 8-bit transformer, an 8GB text encoder, and the VAE). To use the dashboard from another computer, install
Tailscale (free) on both machines and open `http://<machine-name>:8000`.

Start the server so other computers can reach it:

    .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

### Poster sizes (50x70cm)

Measured on an RX 6700 (gfx1031, 9.98GB), not estimated. Full detail in
`probe/RESULT.txt`; re-run it yourself with `.venv\Scripts\python probe_poster.py`.

| Size | dpi on a 50x70 after 4x upscale | Result |
| --- | --- | --- |
| 1440x2016 | 292 | VAE decode never finished in 23 min |
| 960x1344 | 195 | 19.5 min, no tile seams - **use this one** |

Two gfx103x quirks make big images hard on this card, and both are handled in
`pipeline.py` rather than by shrinking the image:

- **Attention.** There is no flash or memory-efficient SDPA kernel, so torch
  falls back to MATH and materialises the whole NxN score matrix - 26.6GB at
  1440x2016. `_chunk_attention_globally()` slices attention over query blocks,
  which is exact rather than approximate because each query row's softmax
  depends only on its own row. Measured 26.6GB -> 2.9GB. Sizes at or below
  1024x1024 skip the wrapper entirely and run the path they always have.
- **VAE decode.** MIOpen has no CK grouped-conv library for gfx1031, so decode
  falls back to a slow reference convolution: roughly 5.4 min per 128x128 latent
  tile. This, not memory, is what caps the usable size. A poster is ~19 min of
  decode against ~5 seconds of denoising.

195 dpi is above the 150 dpi floor normally used for large-format wall art, but
below the 300 dpi a print service may ask for.

## Rate limiting (built in)

Generation is paced at ~2 images/min and stops at 450 images/day to stay
inside Gemini's free tier. Big batches just take a while - paste the list,
walk away, come back to review. The status bar shows today's usage.

## Tests

    .venv/bin/pytest -q

## Share with a few people (any device)

The dashboard runs on this machine; a tunnel gives it a public link so others
can open it in a browser and queue images. Generation still happens locally.

1. Add a password to `.env` on this machine:

       DASHBOARD_PASSWORD=pick-something-only-you-two-know

   The dashboard refuses to start without one. It is the only way in - the
   designs, the images, and the settings are all behind it, not just the
   buttons. Change it by editing this line and restarting; that also signs
   out everyone who was already in.
2. Install `cloudflared` from Cloudflare's site.
3. Run:

       .venv\Scripts\python share.py

That starts the server, opens the tunnel, and prints a
`https://<random>.trycloudflare.com` URL. Share it, and share the password
separately. Anyone who opens the link gets a login page; once past it they stay
signed in on that browser for 30 days, or until you change the password. Ctrl-C
stops both the server and the tunnel.

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
