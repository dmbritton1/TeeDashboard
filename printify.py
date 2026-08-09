"""Printify: upload image, create a product for the design's product type, publish."""
import base64

import requests

import db
import pipeline

API = "https://api.printify.com/v1"
COLORS = {"Black", "White"}
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


def _select_variants(product: str, variants: list) -> list:
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
    return [v for v in variants if v["options"].get("color") in COLORS] or variants[:10]


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
    variants = _select_variants(product, all_variants)

    product_json = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": design["phrase"].title() + " " + data["title_suffix"],
            "description": design["phrase"],
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
