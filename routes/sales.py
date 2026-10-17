import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from auth import get_current_user, require_roles
from database import get_db
from models import Sale
from schemas.sale import SaleCreate, SaleListResponse, SaleResponse, SaleUpdate

# Everyone signed in can read the sales; only admins and managers change them.
router = APIRouter(prefix="/sales", tags=["sales"], dependencies=[Depends(get_current_user)])
writers = Depends(require_roles("admin", "manager"))


def _get_or_404(db: Session, sale_id: int) -> Sale:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.get("", response_model=SaleListResponse, summary="List past sales")
def list_sales(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: str | None = Query(None, description="Matches name, phone, address or product"),
    product: str | None = Query(None, description="Exact purchased product"),
    purchased_from: datetime.date | None = None,
    purchased_to: datetime.date | None = None,
    db: Session = Depends(get_db),
):
    """Paged list of past sales, newest purchase first.
    Filter by search text, exact product name, or a purchase date range. Requires sign-in.
    """
    q = db.query(Sale)
    if search and search.strip():
        like = f"%{search.strip()}%"
        q = q.filter(or_(Sale.name.like(like), Sale.phone.like(like), Sale.alt_phone.like(like),
                         Sale.address.like(like), Sale.purchased_product.like(like)))
    if product:
        q = q.filter(Sale.purchased_product == product)
    if purchased_from:
        q = q.filter(Sale.purchase_date >= purchased_from)
    if purchased_to:
        q = q.filter(Sale.purchase_date <= purchased_to)
    total = q.count()
    items = q.order_by(Sale.purchase_date.desc(), Sale.id.desc()).offset(skip).limit(limit).all()
    return SaleListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/products", response_model=list[str], summary="List purchased product names")
def list_products(db: Session = Depends(get_db)):
    """Distinct product names that appear in past sales. Used for the filter and the form's suggestions."""
    rows = (db.query(Sale.purchased_product).filter(Sale.purchased_product.isnot(None))
            .distinct().order_by(Sale.purchased_product).all())
    return [r[0] for r in rows]


@router.get("/{sale_id}", response_model=SaleResponse, summary="Get a sale")
def get_sale(sale_id: int, db: Session = Depends(get_db)):
    """One past sale record. Returns 404 if it does not exist."""
    return _get_or_404(db, sale_id)


@router.post("", response_model=SaleResponse, status_code=201, dependencies=[writers], summary="Create a sale record")
def create_sale(body: SaleCreate, db: Session = Depends(get_db)):
    """Adds a past sale to the register. Admin and manager only."""
    sale = Sale(**body.model_dump())
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return sale


@router.put("/{sale_id}", response_model=SaleResponse, dependencies=[writers], summary="Update a sale record")
def update_sale(sale_id: int, body: SaleUpdate, db: Session = Depends(get_db)):
    """Partial update of a past sale. Admin and manager only."""
    sale = _get_or_404(db, sale_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status_code=422, detail="Name cannot be empty")
    for field, value in changes.items():
        setattr(sale, field, value)
    db.commit()
    db.refresh(sale)
    return sale


@router.delete("/{sale_id}", status_code=204, dependencies=[writers], summary="Delete a sale record")
def delete_sale(sale_id: int, db: Session = Depends(get_db)):
    """Deletes a past sale record. Admin and manager only."""
    db.delete(_get_or_404(db, sale_id))
    db.commit()
