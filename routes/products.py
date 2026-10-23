import datetime
import re
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from auth import get_current_user
from database import get_db
from models import Employee, Product, ProductOption
from schemas.product import (
    OptionKind,
    ProductCreate,
    ProductListResponse,
    ProductOptionCreate,
    ProductOptionResponse,
    ProductResponse,
    ProductUpdate,
)

router = APIRouter(prefix="/products", tags=["products"])

# Admins and managers edit the catalogue; every signed-in employee can read it.
can_edit = get_current_user

_BOOL_COLUMNS = ("is_popular", "is_active", "tax_included")
_OPTION_COLUMNS = ("material_id",)
_with_options = (joinedload(Product.material),)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "option"


def _to_response(product: Product) -> ProductResponse:
    response = ProductResponse.model_validate(product)
    mrp, sale = product.mrp, product.sale_price
    if mrp and sale is not None and mrp > 0:
        response.you_save = mrp - sale
        response.discount_percent = ((mrp - sale) * 100 / mrp).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return response


def _get_product(db: Session, product_id: int) -> Product:
    product = (
        db.query(Product)
        .options(*_with_options)
        .filter(Product.id == product_id, Product.deleted_at.is_(None))
        .first()
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def _check_options(db: Session, data: dict) -> None:
    """material_id must point at an active material option."""
    option_id = data.get("material_id")
    if option_id is None:
        return
    found = db.query(ProductOption.id).filter(
        ProductOption.id == option_id, ProductOption.kind == "material", ProductOption.is_active == 1
    ).first()
    if found is None:
        raise HTTPException(status_code=422, detail="Unknown material")


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        message = str(error.orig).lower()
        if "sku" in message:
            raise HTTPException(status_code=409, detail="Another product already uses this SKU")
        if "slug" in message:
            raise HTTPException(status_code=409, detail="Another product already uses this slug")
        raise HTTPException(status_code=409, detail="This product conflicts with an existing one")


@router.get("/options", response_model=list[ProductOptionResponse], summary="List product dropdown values")
def list_options(
    kind: OptionKind | None = None,
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """Brand, category and material options for the product form. Filter by `kind` to get one of them."""
    query = db.query(ProductOption).filter(ProductOption.is_active == 1)
    if kind:
        query = query.filter(ProductOption.kind == kind)
    return query.order_by(ProductOption.name).all()


@router.post("/options", response_model=ProductOptionResponse, status_code=status.HTTP_201_CREATED, summary="Add a product option")
def create_option(
    body: ProductOptionCreate,
    db: Session = Depends(get_db),
    _: Employee = Depends(can_edit),
):
    """Adds a brand, category or material option. Admin and manager only."""
    name = body.name.strip()
    slug = _slugify(name)
    existing = db.query(ProductOption).filter(ProductOption.kind == body.kind, ProductOption.slug == slug).first()
    if existing:
        if existing.is_active:
            return existing  # adding "Ventino" twice just returns the one already there
        existing.is_active = 1
        existing.name = name
        db.commit()
        return existing
    option = ProductOption(kind=body.kind, name=name, slug=slug)
    db.add(option)
    db.commit()
    db.refresh(option)
    return option


@router.get("", response_model=ProductListResponse, summary="List products")
def list_products(
    q: str | None = None,
    material_id: int | None = None,
    is_active: bool | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=500),
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """Paged product list with optional search and filters for material and active state. Soft-deleted products are excluded."""
    query = db.query(Product).filter(Product.deleted_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(Product.name.like(like), Product.model_number.like(like), Product.sku.like(like)))
    if material_id is not None:
        query = query.filter(Product.material_id == material_id)
    if is_active is not None:
        query = query.filter(Product.is_active == int(is_active))
    total = query.count()
    items = (
        query.options(joinedload(Product.material))
        .order_by(Product.name)
        .offset(skip)
        .limit(limit)
        .all()
    )
    return {"items": items, "total": total, "skip": skip, "limit": limit}


@router.get("/{product_id}", response_model=ProductResponse, summary="Get a product")
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: Employee = Depends(get_current_user),
):
    """One product with its specs, pricing and options. Returns 404 if it is unknown or deleted."""
    return _to_response(_get_product(db, product_id))


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED, summary="Create a product")
def create_product(
    body: ProductCreate,
    db: Session = Depends(get_db),
    _: Employee = Depends(can_edit),
):
    """Adds a product to the catalogue. Admin and manager only."""
    data = body.model_dump()
    _check_options(db, data)
    for column in _BOOL_COLUMNS:
        data[column] = int(data[column])
    product = Product(**data)
    db.add(product)
    _commit(db)
    return _to_response(_get_product(db, product.id))


@router.patch("/{product_id}", response_model=ProductResponse, summary="Update a product")
def update_product(
    product_id: int,
    body: ProductUpdate,
    db: Session = Depends(get_db),
    _: Employee = Depends(can_edit),
):
    """Partial update: only the fields sent are changed. Admin and manager only."""
    product = _get_product(db, product_id)
    changes = body.model_dump(exclude_unset=True)

    # name and slug are required, so they can't be cleared
    for column in ("name", "slug"):
        if column in changes and not changes[column]:
            raise HTTPException(status_code=422, detail=f"{column.replace('_', ' ').capitalize()} cannot be empty")

    mrp = changes.get("mrp", product.mrp)
    sale_price = changes.get("sale_price", product.sale_price)
    if mrp is not None and sale_price is not None and sale_price > mrp:
        raise HTTPException(status_code=422, detail="Sale price cannot be higher than MRP")

    _check_options(db, {key: changes[key] for key in _OPTION_COLUMNS if key in changes})
    for column in _BOOL_COLUMNS:
        if column in changes and changes[column] is not None:
            changes[column] = int(changes[column])
    for column, value in changes.items():
        setattr(product, column, value)
    _commit(db)
    db.expire_all()
    return _to_response(_get_product(db, product_id))


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a product")
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: Employee = Depends(can_edit),
):
    """Soft delete. The row stays, but the product disappears from lists. Admin and manager only."""
    product = _get_product(db, product_id)
    product.deleted_at = datetime.datetime.now()
    product.is_active = 0
    db.commit()
