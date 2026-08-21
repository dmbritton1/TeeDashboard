# Mockup studio

**Date:** 2026-08-20
**Status:** Approved design, ready for implementation plan
**Depends on:** the listing-preview spec (adds a panel to its preview)

## Goal

Own the listing images. Upload your own mockup template photos once, mark the
print area, and have every published design composited onto them automatically
and pushed to the Etsy listing — with a default lineup per product and a
per-listing override.

Two requirements drive every decision below:

1. **The listings must look unique.** Every Printify seller on Etsy uses the
   same stock lifestyle photos.
2. **No opening Printify's web UI per design.** This is what rules out the
   otherwise-obvious route.

## Context: what Printify's API will and will not do

Verified against the published API reference, not assumed:

- The product's `images` array is **READ-ONLY**. *"Mock-up images are read only
  values."* There is no mockup endpoint anywhere in the spec — the full path
  list contains catalog, shops, products, orders, personalization, uploads and
  webhooks, and nothing else. `/v1/uploads/images.json` takes print files.
- The Mockup Library — where you tick *Folded 2*, *Lifestyle 2*, *Hanging 1* —
  is UI-only. It cannot be driven by the API.
- Printify's **My Uploads** accepts custom mockups, but only *finished* images
  (JPG/PNG/SVG, up to 30,000×30,000). It will not place a design onto a template
  you supply — that is the Product Creator, and it only uses Printify's own
  catalogue templates. And it is per-product manual clicking, which requirement
  2 forbids.

So: we composite, and we deliver through Etsy.

### The link between the two systems

A published Printify product carries `external[0].id` — the sales channel's own
identifier, i.e. **the Etsy listing ID**, populated when publishing succeeds.
That is the join, and it is the only thing that makes this design possible.

Etsy's side, confirmed from the OpenAPI spec:

```
POST   /v3/application/shops/{shop_id}/listings/{listing_id}/images
DELETE /v3/application/shops/{shop_id}/listings/{listing_id}/images/{listing_image_id}
```

`uploadListingImage` is `multipart/form-data` taking `image`, `rank`,
`overwrite`, `alt_text` (max 500 chars). Scope `listings_w`. Etsy allows 10
images per listing; rank 1 is the thumbnail.

## Design

### Storage

A `mockups` table, plus one column on `designs`:

| Column | Purpose |
|---|---|
| `id` | |
| `name` | operator's label, e.g. "Standing model, white tee" |
| `product` | `tee` or `poster` — which designs this template applies to |
| `file` | the uploaded template photo |
| `corners` | JSON, four `(x, y)` pairs as **fractions** of image size |
| `enabled` | in the default lineup or not |
| `rank` | position in the lineup; becomes the Etsy image rank |

`designs.mockup_ids TEXT` — comma-separated template IDs. **Empty means "use the
product's default lineup."** Null-means-inherit keeps the common case free of
bookkeeping: change the lineup and every listing that never overrode it follows.

Corners are stored as fractions, not pixels, so resizing or re-encoding a
template photo cannot silently move the print area.

### The Mockups tab

Upload a photo, click its four corners, name it, pick the product. That is the
whole setup, once per template, reused forever.

The lineup is the same list with `enabled` and `rank`, filtered per product —
tee templates rank 1..n, poster templates rank 1..n. Capped at 10, because Etsy
is.

### Compositing (`mockup.py`)

Four steps, and the middle two are what stop it looking like a sticker:

1. **Fit, then warp.** Scale the artwork to fit *inside* the marked quad
   preserving its aspect ratio, centred, then perspective-transform it into the
   quad (`Image.transform(..., Image.PERSPECTIVE, coeffs)`). Stretching to fill
   is an explicit opt-in, never the default — invisible on a tee, glaring on a
   framed poster.

2. **Displace by the photo's own luminance.** On a plain-coloured garment a
   wrinkle is bright on one side and dark on the other *because of its shape*,
   so the photo's high-passed luminance approximates the fabric's height field.
   Take its gradient as a displacement vector and resample the artwork through
   it, so a straight line bends as it crosses a fold. `torch.nn.functional.
   grid_sample` does the resampling and torch is already a dependency; no scipy,
   no authored displacement map, no work from the operator beyond the photo.

3. **Multiply the shading back.** The blurred luminance, normalised about its
   own mean, multiplied over the artwork, so a print sitting in a shadow
   darkens.

4. **Composite and save** as JPEG.

Cached on disk per `(design_id, template_id, template mtime)` so the preview
does not recomposite on every open.

Posters need only steps 1, 3 and 4 — paper is flat, so displacement is a no-op
and the shading pass picks up glass glare and frame shadows for free. The same
code path handles both; nothing is poster-specific.

**Known ceiling, stated plainly:** the derived maps are an approximation of
hand-authored ones. They hold up on a flat chest print on a worn shirt and
degrade on heavily folded or crumpled templates. There is no obstruction mask,
so an arm or collar crossing the print area will be painted over — mark print
areas that sit clear of both. Both are clean upgrades if the output demands
them: an optional per-template PNG mask, and an optional authored displacement
map that overrides the derived one.

### Delivery (`etsy.py`)

OAuth 2.0 authorization code with PKCE:

```
authorize  https://www.etsy.com/oauth/connect
token      https://openapi.etsy.com/v3/public/oauth/token
header     x-api-key: <keystring>
scopes     listings_r listings_w
```

Access tokens last an hour, refresh tokens 90 days. The refresh token is stored
in settings and rotated; the access token is cached in memory with its expiry.
The Etsy shop ID is resolved once at connect time and stored.

After a successful publish, a background job:

1. Polls the Printify product until `external[0].id` appears.
2. Resolves the lineup: the design's `mockup_ids`, or the product default.
3. Composites the design against each template.
4. Uploads them at ranks 1..n with `overwrite=true`, with `alt_text` drawn from
   the listing title.

Printify's stock mockups fall to positions 6–10. The operator never opens
Printify's UI.

### Panel in the listing preview

The preview gains a mockup strip: the actual composited images for *this*
design, tickable and reorderable, saved to `designs.mockup_ids` through the
PATCH route the preview already uses. Empty selection falls back to the default
lineup rather than publishing no mockups.

This is why the mockups are worth rendering in our own dashboard when the
Printify ones were not: these are images Printify never sees, so there is no
other place to look at them.

## Redirect URI: resolved

Etsy's own documentation says the callback "must implement TLS and use an
`https://` prefix" — but that word is *typically*, and it is guidance rather
than a validator rule. In practice Etsy accepts an `http://localhost` callback:
two independent public implementations register and use
`http://localhost:3003/oauth/redirect`, and one has a maintenance history
(a June 2024 fix to an auth loop) showing it genuinely runs.

So the dashboard registers

```
http://localhost:8000/etsy/callback
```

in the Etsy app dashboard, matching the port `share.py` and the README already
use. Connecting is a button in Settings, done once, from a browser on the
machine running the dashboard — not over the tunnel, since the tunnel URL
changes between runs and every redirect URI must match its registration
exactly (case-sensitively).

This rests on community evidence, not an Etsy guarantee. If Etsy tightens it,
the fallback is the manual one: send the operator to the authorize URL and have
them paste the `code` parameter back into Settings. Worth keeping the code path
shaped so that fallback is a small change rather than a rewrite — the token
exchange does not care where the code came from.

## Testing

- `corners` round-trip through fractions at two different image sizes.
- Aspect preservation: a 5:7 artwork into a 1:1 quad is letterboxed, not
  stretched.
- Perspective coefficients against a known quad, and the identity case.
- Displacement is a no-op on a uniform grey template (no gradient, no movement)
  — the check that catches a sign or axis error.
- Lineup resolution: empty `mockup_ids` inherits; a set one overrides; a
  deleted template drops out of both without breaking publish.
- Etsy client with `requests` mocked: rank assignment, refresh-on-401, and that
  an expired refresh token surfaces as a clear "reconnect Etsy" rather than a
  stack trace.
- No test touches the network, matching the existing Printify and Gemini
  harnesses.

## Out of scope

Obstruction masks and authored displacement maps (upgrades, noted above).
Garment-colour swapping — upload a black-shirt photo if you sell black shirts.
Generating template photos with the local FLUX setup, though the pipeline could
plainly do it. A template marketplace or sharing.

## Blockers

The stored **Printify token is dead** — every authenticated endpoint 401s. No
part of the delivery chain can be tested end to end until it is replaced. The
studio and compositing halves are fully testable without it.

Posters additionally have no Printify blueprint configured, because the
catalogue endpoint 401s on that same token. Nothing mockup-specific — the same
new token unblocks both.
