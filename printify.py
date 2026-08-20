"""Printify: upload image, create a product for the design's product type, publish."""
import base64

import requests

import db
import listing
import pipeline

API = "https://api.printify.com/v1"
# Printify renders mockups per enabled colour, so a narrow list is a direct
# cause of a thin mockup set on the listing.
DEFAULT_TEE_COLORS = ("Black", "White", "Navy", "Sport Grey", "Sand")
# The 50x70cm variant's real name is unverified: this account's token 401s on the
# poster catalogue, so these are plausible spellings rather than a fact. Once a
# token with shop scope exists, correct this tuple - it is the only place to look.
# Haystacks are lower-cased with spaces stripped before matching, so "50 x 70 cm"
# matches "50x70" without needing its own entry.
POSTER_VARIANT_MATCH = ("50x70", "19.7x27.6")


def _headers() -> dict:
    return {"Authorization": "Bearer %s" % db.get_setting("printify_api_token")}


def _get(path: str):
    r = requests.get(API + path, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict, timeout: int = 60):
    r = requests.post(API + path, headers=_headers(), json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _blueprint(data: dict) -> int:
    """Which Printify product this design becomes."""
    raw = db.get_setting(data["blueprint_key"]) or data["blueprint_default"]
    if not raw:
        raise RuntimeError(
            "No Printify blueprint configured for %s - set it in settings" % data["label"]
        )
    return int(raw)


def tee_colors() -> list[str]:
    """Colours to enable on a tee. Names must match the print provider's
    catalogue exactly; anything it doesn't recognise hits _select_variants'
    existing fallback rather than publishing nothing."""
    raw = db.get_setting("tee_colors") or ""
    return [c.strip() for c in raw.split(",") if c.strip()] or list(DEFAULT_TEE_COLORS)


def _select_variants(product: str, variants: list, colors: list) -> list:
    """The sellable variants for this product. Both paths keep the existing
    'or the first ten' fallback: against an unfamiliar catalogue, publishing
    narrow beats publishing nothing."""
    if product == "poster":
        for needle in POSTER_VARIANT_MATCH:
            hit = [v for v in variants
                   if needle in str(v["options"].get("size", "")).replace(" ", "").lower()]
            if hit:
                return hit
        return variants[:10]
    return [v for v in variants if v["options"].get("color") in set(colors)] or variants[:10]


def _description(design: dict) -> str:
    """Generated hook plus the operator's fixed block.

    The boilerplate is a setting rather than model output on purpose: paper
    weight, sizes and delivery are promises to a buyer, and a model asked to
    write them invents them.
    """
    parts = [p.strip() for p in (design.get("listing_hook"),
                                 db.get_setting("listing_boilerplate")) if p and p.strip()]
    return "\n\n".join(parts) if parts else design["phrase"]


def listing_fields(design: dict) -> dict:
    """Everything about a listing that is decided before we talk to Printify.

    Both the publish payload and the preview endpoint read this, so the
    operator cannot be shown a listing that differs from the one that ships.
    Deliberately excludes blueprint and print provider: those need the network,
    and _blueprint() raises for a product with none configured, which would
    turn a preview of an unconfigured poster into a 500.
    """
    product = design.get("product") or pipeline.DEFAULT_PRODUCT
    data = pipeline.product_data(product)
    return {
        "title": design.get("listing_title")
                 or design["phrase"].title() + " " + data["title_suffix"],
        "description": _description(design),
        "tags": listing.clean_tags(design.get("listing_tags") or ""),
        "price_cents": data["price_cents"],
        "colors": tee_colors() if product == "tee" else [],
        "product_label": data["label"],
    }


def publish(design: dict) -> str:
    shop_id = db.get_setting("printify_shop_id")
    product = design.get("product") or pipeline.DEFAULT_PRODUCT
    data = pipeline.product_data(product)
    blueprint_id = _blueprint(data)
    file_path = design.get("print_file") or design["file"]

    with open(file_path, "rb") as f:
        contents = base64.b64encode(f.read()).decode()
    image_id = _post(
        "/uploads/images.json",
        {"file_name": "design-%s.png" % design["id"], "contents": contents},
        timeout=120,
    )["id"]

    providers = _get("/catalog/blueprints/%d/print_providers.json" % blueprint_id)
    if not providers:
        raise RuntimeError("No print providers for blueprint %d" % blueprint_id)
    pp_id = providers[0]["id"]

    all_variants = _get(
        "/catalog/blueprints/%d/print_providers/%d/variants.json" % (blueprint_id, pp_id)
    )["variants"]
    variants = _select_variants(product, all_variants, tee_colors())

    fields = listing_fields(design)
    product_json = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": fields["title"],
            "description": fields["description"],
            "tags": fields["tags"],
            "blueprint_id": blueprint_id,
            "print_provider_id": pp_id,
            "variants": [
                {"id": v["id"], "price": data["price_cents"], "is_enabled": True}
                for v in variants
            ],
            "print_areas": [
                {
                    "variant_ids": [v["id"] for v in variants],
                    "placeholders": [
                        {
                            "position": "front",
                            "images": [
                                {"id": image_id, "x": 0.5, "y": 0.5, "scale": 1.0, "angle": 0}
                            ],
                        }
                    ],
                }
            ],
        },
    )
    product_id = product_json["id"]

    _post(
        "/shops/%s/products/%s/publish.json" % (shop_id, product_id),
        {"title": True, "description": True, "images": True, "variants": True, "tags": True},
    )
    return product_id
