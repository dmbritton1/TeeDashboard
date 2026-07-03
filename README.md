# T-Shirt Design Pipeline

Paste Etsy search phrases + style filters, generate design candidates with
Gemini (free tier), review them in a dashboard, approve the keepers
(auto-upscaled locally for print), and publish to Printify -> Etsy.

## Run it

    .venv/bin/uvicorn main:app --port 8000

Open http://localhost:8000

First-time setup:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

## Configure

Paste keys in the dashboard settings panel (stored in the local SQLite db):

- **Gemini API key** - free, from https://aistudio.google.com (required)
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

## Rate limiting (built in)

Generation is paced at ~2 images/min and stops at 450 images/day to stay
inside Gemini's free tier. Big batches just take a while - paste the list,
walk away, come back to review. The status bar shows today's usage.

## Tests

    .venv/bin/pytest -q
