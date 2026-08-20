# Listing preview before publish

**Date:** 2026-08-20
**Status:** Approved design, ready for implementation plan
**Depends on:** PR #1 (`claude/dashboard-setup-578b0f`) — Etsy listing copy

## Goal

Show the operator what a listing will look like before it goes to Etsy, and let
them fix it there. Today `printify.publish()` assembles a title, description,
price and variant list in-process and fires it at Printify; nothing is ever
displayed. The first time you see your own listing is on Etsy.

Two smaller corrections ride along, because both are lies the dashboard
currently tells: the hardcoded Black/White colour list and the
`printify_ready` flag that means "a token exists" rather than "a token works".

## Context

PR #1 already solved the copy half. On approve, `listing.write()` calls Gemma
once and stores `listing_title`, `listing_tags` (13, Etsy's cap) and
`listing_hook`; the description sent to Printify is the hook plus an
operator-authored boilerplate block from Settings. All three are editable on the
approved card, saved on blur through `PATCH /api/designs/{id}`.

What PR #1 does not do is *show* the listing. The copy lives in three textareas
inside a collapsed `<details class="prompt listing">` accordion. It is a form,
not a preview.

### What publishing looks like today

| Location | Behaviour |
|---|---|
| `main.py` `approve()` | flips status, stamps `reviewed_at`, fires `upscale.upscale` |
| `printify.py` `publish()` | builds title/description/variants/print_areas inline, POSTs, publishes |
| `printify.py:11` `COLORS` | `{"Black", "White"}` — hardcoded |
| `printify.py:74` | `pp_id = providers[0]["id"]` — whichever the catalogue lists first |
| `main.py` `status()` | `printify_ready = bool(token and shop_id)` — presence, not validity |

### The blocker

PR #1 reports the stored Printify token is **dead** — rejected, not expired.
Its JWT claims are healthy but every authenticated endpoint 401s. Nothing in
this spec can be verified against a live shop until a new token is generated.
The preview itself is testable without one; publishing is not.

## Design

### One source of truth for the listing

Pull the payload assembly out of `printify.publish()` into

```
printify.listing_fields(design) -> dict
```

returning exactly what gets sent: `title`, `description`, `tags`, `price_cents`,
`colors`, `blueprint_id`, `blueprint_label`. `publish()` calls it; a new
read-only endpoint

```
GET /api/designs/{id}/listing
```

returns it to the browser.

This is the load-bearing decision. The alternative — rebuilding the listing in
JavaScript — would duplicate the description-assembly rule and the colour list
in a second language, and the two would drift the first time either changed. A
preview that can disagree with what publishes is worse than no preview.

### The preview itself

Approved cards get a **Preview listing** button that opens the existing
lightbox. Left: the artwork. Right: the listing.

- Title, editable, with a live `112/140` counter.
- Tags as chips, editable, `13/13` counter. `listing.clean_tags` already
  enforces lower-casing, de-duplication and the 20-character drop; the counter
  reflects what survives, not what was typed.
- Description: hook and boilerplate already joined, as Printify receives it.
- Price, colours, blueprint name — read-only context.

Fields save on blur through PR #1's existing delegated PATCH handler. No new
save path, and `syncChildren`'s focus guard already protects a field being
typed in.

The collapsed accordion is **removed**, not kept alongside. Two editing
surfaces for the same three fields is how they diverge.

While Gemma is still writing, the button reads `writing copy…` and is disabled,
reusing `listingBox`'s existing in-flight rule (a `reviewed_at` inside three
minutes) so a design approved before this feature existed doesn't promise copy
that will never arrive.

### Colours and print provider become settings

`COLORS` becomes a `tee_colors` setting, defaulting to
`Black, White, Navy, Sport Grey, Sand`. Printify renders mockups per enabled
colour, so Black/White alone is a direct cause of a thin mockup set.

`providers[0]` becomes a `printify_print_provider_id` setting; blank keeps
today's behaviour exactly. Providers differ in both mockup libraries and price,
so which one you get should not be an accident of catalogue ordering.

Both keep `_select_variants`'s existing habit: an unrecognised colour name
falls back to the first ten variants rather than publishing nothing.

### An honest connection status

`printify_ready` becomes "we checked and it worked":

- Verified on `POST /api/settings` when the token or shop ID changes, reusing
  the `/api/test/printify` logic.
- Set false when a publish returns 401.
- Stored in settings and read from there.

Never verified inside `GET /api/status`, which the dashboard polls every three
seconds. A network call in that path would put a multi-second hang between the
operator and their own dashboard.

## Testing

- `listing_fields` returns the same dict that `publish()` sends — asserted
  against the POST body captured from the existing mocked-requests harness, so
  the two cannot drift without a red test.
- `tee_colors` parsing: whitespace, empty, and unknown-colour fallback.
- `GET /api/designs/{id}/listing` on a non-approved design and a missing design.
- `printify_ready` reflects the stored check, and a 401 on publish clears it.
- Front end verified in the browser: counter accuracy at the 140/13/20
  boundaries, and that a field being typed in survives the three-second poll.

## Out of scope

Mockups — the entire mockup question lives in the mockup-studio spec, which
adds its own panel to this preview once it exists. Regenerating copy (today:
unreview then approve). Size ladders, bundles and framing upsells. Etsy
storefront settings, which are not software.
