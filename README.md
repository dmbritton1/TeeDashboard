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

On a computer with an NVIDIA graphics card (12GB+ VRAM recommended), the app
generates images locally with FLUX.1-schnell instead of calling Gemini - no
API key, no daily cap, no per-image cost. Setup on that machine:

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
