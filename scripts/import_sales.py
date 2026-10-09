"""Load buyers_register.xlsx into the sales table.  Run from ogera_crm_backend:
    python -m scripts.import_sales [path]
Refuses to run when the table already has rows, so a second run cannot duplicate them."""
import datetime
import sys

import openpyxl

from database import Base, SessionLocal, engine
import models  # noqa: F401
from models import Sale


def clean(value):
    text = str(value).strip() if value is not None else ""
    return text or None


def main(path: str) -> None:
    Base.metadata.create_all(bind=engine, tables=[Sale.__table__])
    rows = list(openpyxl.load_workbook(path, read_only=True).active.iter_rows(values_only=True))[1:]
    db = SessionLocal()
    try:
        if db.query(Sale).count():
            sys.exit("sales table is not empty; nothing imported")
        for date, name, phone, alt, _raw, address in rows:
            if not clean(name):
                continue
            db.add(Sale(
                purchase_date=datetime.date.fromisoformat(clean(date)) if clean(date) else None,
                name=clean(name), phone=clean(phone), alt_phone=clean(alt), address=clean(address),
            ))
        db.commit()
        print("imported", db.query(Sale).count())
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "buyers_register.xlsx")
