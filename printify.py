"""Printify: upload image, create t-shirt product, publish to the connected Etsy shop."""
import base64

import requests

import db

API = "https://api.printify.com/v1"
BLUEPRINT_ID = 6  # Unisex Heavy Cotton Tee (Gildan 5000)
COLORS = {"Black", "White"}
PRICE_CENTS = 2499  # $24.99 default; tune later per product


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


def publish(design: dict) -> str:
    shop_id = db.get_setting("printify_shop_id")
    file_path = design.get("print_file") or design["file"]

    with open(file_path, "rb") as f:
        contents = base64.b64encode(f.read()).decode()
    image_id = _post(
        "/uploads/images.json",
        {"file_name": "design-%s.png" % design["id"], "contents": contents},
        timeout=120,
    )["id"]

    providers = _get("/catalog/blueprints/%d/print_providers.json" % BLUEPRINT_ID)
    if not providers:
        raise RuntimeError("No print providers for blueprint %d" % BLUEPRINT_ID)
    pp_id = providers[0]["id"]

    all_variants = _get(
        "/catalog/blueprints/%d/print_providers/%d/variants.json" % (BLUEPRINT_ID, pp_id)
    )["variants"]
    variants = [v for v in all_variants if v["options"].get("color") in COLORS] or all_variants[:10]

    product = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": design["phrase"].title() + " T-Shirt",
            "description": design["phrase"],
            "blueprint_id": BLUEPRINT_ID,
            "print_provider_id": pp_id,
            "variants": [
                {"id": v["id"], "price": PRICE_CENTS, "is_enabled": True} for v in variants
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
    product_id = product["id"]

    _post(
        "/shops/%s/products/%s/publish.json" % (shop_id, product_id),
        {"title": True, "description": True, "images": True, "variants": True, "tags": True},
    )
    return product_id
