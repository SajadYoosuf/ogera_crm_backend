"""
Import products from ventino-products.csv into the database.

Run from the backend directory:
    python scripts/import_products.py

What it does:
- Creates a "Stainless Steel" material option if it doesn't exist
- Inserts each product row from the CSV
- Skips any product whose slug already exists (safe to re-run)
"""

import csv
import sys
from pathlib import Path
from decimal import Decimal

# Make sure the backend modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from models import Product, ProductOption

CSV_PATH = Path(__file__).resolve().parent.parent.parent / "ventino-products.csv"

ORIENTATION_MAP = {
    "vertical": "vertical",
    "horizontal": "horizontal",
    "vertical-sm": "vertical",   # SM (small mouth) variant — treated as vertical
}


def get_or_create_material(db, name: str) -> int:
    slug = name.lower().replace(" ", "-")
    existing = db.query(ProductOption).filter_by(kind="material", slug=slug).first()
    if existing:
        if not existing.is_active:
            existing.is_active = 1
            db.commit()
        return existing.id
    option = ProductOption(kind="material", name=name, slug=slug, is_active=1)
    db.add(option)
    db.commit()
    db.refresh(option)
    print(f"  Created material option: {name} (id={option.id})")
    return option.id


def main():
    if not CSV_PATH.exists():
        print(f"CSV not found at: {CSV_PATH}")
        sys.exit(1)

    db = SessionLocal()
    try:
        material_id = get_or_create_material(db, "Stainless Steel")

        inserted = 0
        skipped = 0

        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                slug = row["slug"].strip()

                # Skip if already in DB
                if db.query(Product.id).filter_by(slug=slug).first():
                    print(f"  SKIP (exists): {slug}")
                    skipped += 1
                    continue

                capacity = int(row["capacity_ltr"]) if row["capacity_ltr"].strip() else None
                orientation_raw = row["orientation"].strip().lower()
                orientation = ORIENTATION_MAP.get(orientation_raw, "vertical")

                mrp_str = row["mrp_inr"].strip()
                sale_str = row["selling_price_inr"].strip()
                mrp = Decimal(mrp_str) if mrp_str else None
                sale_price = Decimal(sale_str) if sale_str else None

                model_number = row["model"].strip() or None
                tagline = row["tagline"].strip() or None
                short_description = row["description"].strip() or None

                product = Product(
                    name=row["name"].strip(),
                    slug=slug,
                    material_id=material_id,
                    tagline=tagline,
                    short_description=short_description,
                    model_number=model_number,
                    capacity_litres=capacity,
                    capacity_display=f"{capacity} L" if capacity else None,
                    orientation=orientation,
                    mrp=mrp,
                    sale_price=sale_price,
                    features=[],
                    is_popular=0,
                    is_active=1,
                    tax_included=1,
                )
                db.add(product)
                db.commit()
                db.refresh(product)
                print(f"  INSERTED (id={product.id}): {product.name}")
                inserted += 1

        print(f"\nDone. Inserted: {inserted}, Skipped: {skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
