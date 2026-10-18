from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import Business, Category, Product, ProductImage, User
from app.schemas import ProductImageOut, ProductListResponse, ProductOut
from app.services import seller_plans as plan_service
from app.uploads import public_media_url, save_product_images

router = APIRouter(prefix="/products", tags=["products"])

ALLOWED_CONDITIONS = {"new", "like_new", "good", "fair"}
ALLOWED_PARCEL_SIZES = {"small", "medium", "large"}

_category_path = selectinload(Product.category).selectinload(Category.parent).selectinload(
    Category.parent
)


def _needs_custom_category(category: Category) -> bool:
    slug = (category.slug or "").lower()
    return slug == "other" or slug.startswith("other-")


def _product_out(product: Product, *, distance_km: float | None = None) -> ProductOut:
    seller_name = None
    if product.seller:
        seller_name = product.seller.display_name or product.seller.email
    listed_by_name = None
    if product.listed_by:
        listed_by_name = product.listed_by.display_name or product.listed_by.email
    category_name = product.category.name if product.category else None
    main_name = None
    mid_name = None
    if product.category:
        parent = product.category.parent
        if parent is None:
            main_name = product.category.name
        elif parent.parent is None:
            main_name = parent.name
        else:
            mid_name = parent.name
            main_name = parent.parent.name if parent.parent else None

    return ProductOut(
        id=product.id,
        seller_id=product.seller_id,
        seller_name=seller_name,
        listed_by_id=product.listed_by_id,
        listed_by_name=listed_by_name,
        business_id=product.business_id,
        category_id=product.category_id,
        category_name=category_name,
        main_category_name=main_name,
        mid_category_name=mid_name,
        title=product.title,
        description=product.description,
        condition=product.condition or "good",
        brand=product.brand,
        color=product.color,
        parcel_size=product.parcel_size,
        custom_category=product.custom_category,
        price=product.price,
        currency=product.currency,
        zone=product.zone or "pakistan",
        province=product.province,
        city=product.city,
        shipping_within_city=product.shipping_within_city,
        shipping_out_of_city=product.shipping_out_of_city,
        latitude=product.latitude,
        longitude=product.longitude,
        distance_km=distance_km,
        images=[
            ProductImageOut(
                id=img.id,
                url=public_media_url(img.url) or img.url,
                sort_order=img.sort_order,
            )
            for img in (product.images or [])
        ],
        is_active=product.is_active,
    )


async def _load_product(db: AsyncSession, product_id: int) -> Product | None:
    result = await db.execute(
        select(Product)
        .options(
            selectinload(Product.seller),
            selectinload(Product.listed_by),
            selectinload(Product.images),
            _category_path,
        )
        .where(Product.id == product_id)
    )
    return result.scalar_one_or_none()


def _product_load_options():
    return (
        selectinload(Product.seller),
        selectinload(Product.listed_by),
        selectinload(Product.images),
        _category_path,
    )


async def _descendant_category_ids(db: AsyncSession, cat: Category) -> list[int]:
    """Category id plus all direct children and grandchildren."""
    child_ids = (
        await db.execute(select(Category.id).where(Category.parent_id == cat.id))
    ).scalars().all()
    if not child_ids:
        return [cat.id]
    grand_ids = (
        await db.execute(select(Category.id).where(Category.parent_id.in_(child_ids)))
    ).scalars().all()
    return [cat.id, *child_ids, *grand_ids]


@router.post("", response_model=ProductOut, status_code=201)
async def create_product(
    title: str = Form(...),
    description: str = Form(...),
    price: str = Form(...),
    categoryId: int = Form(...),
    province: str = Form(...),
    city: str = Form(...),
    zone: str = Form("pakistan"),
    condition: str = Form("good"),
    brand: str | None = Form(None),
    color: str | None = Form(None),
    parcelSize: str | None = Form(None),
    customCategory: str | None = Form(None),
    shippingWithinCity: str = Form("0"),
    shippingOutOfCity: str = Form("0"),
    currency: str = Form("PKR"),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    photos: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = user.role or "user"
    if role == "admin":
        raise HTTPException(status_code=403, detail="Admin cannot list products")
    if role == "business":
        raise HTTPException(
            status_code=403,
            detail="Business sells through PL — assign a PL to list products",
        )

    try:
        quota_user, listing_zone = await plan_service.assert_can_create_listing(
            db, user, zone=zone, category_id=categoryId
        )
    except plan_service.PlanError as exc:
        raise plan_service.plan_error_http(exc) from exc

    try:
        price_val = Decimal(price)
        ship_in = Decimal(shippingWithinCity or "0")
        ship_out = Decimal(shippingOutOfCity or "0")
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=400, detail="Invalid price or shipping amount")

    if price_val <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than 0")

    cond = (condition or "good").strip().lower()
    if cond not in ALLOWED_CONDITIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Condition must be one of: {', '.join(sorted(ALLOWED_CONDITIONS))}",
        )

    brand_val = (brand or "").strip() or None
    if brand_val and len(brand_val) > 120:
        raise HTTPException(status_code=400, detail="Brand must be 120 characters or less")

    color_val = (color or "").strip().lower() or None
    if color_val and len(color_val) > 64:
        raise HTTPException(status_code=400, detail="Color must be 64 characters or less")

    parcel_val = (parcelSize or "").strip().lower() or None
    if parcel_val and parcel_val not in ALLOWED_PARCEL_SIZES:
        raise HTTPException(
            status_code=400,
            detail=f"Parcel size must be one of: {', '.join(sorted(ALLOWED_PARCEL_SIZES))}",
        )

    category = await db.get(Category, categoryId)
    if not category or category.parent_id is None:
        raise HTTPException(
            status_code=400,
            detail="Pick a subcategory (not a main category)",
        )
    if category.is_virtual_all:
        raise HTTPException(
            status_code=400,
            detail="Cannot list under 'View all' — choose a specific type",
        )
    # Must be a leaf (no children) — e.g. Outerwear, not Clothing
    has_kids = (
        await db.execute(
            select(Category.id).where(Category.parent_id == category.id).limit(1)
        )
    ).first()
    if has_kids:
        raise HTTPException(
            status_code=400,
            detail="Pick a more specific type (e.g. Outerwear, not Clothing)",
        )

    custom = (customCategory or "").strip() or None
    if _needs_custom_category(category):
        if not custom or len(custom) < 2:
            raise HTTPException(
                status_code=400,
                detail="Describe the type (required when you choose Other)",
            )
        if len(custom) > 120:
            raise HTTPException(status_code=400, detail="Custom type must be 120 characters or less")
    else:
        custom = None

    # Attribution: PL lists → business owner is seller; peer user lists for self
    seller_id = user.id
    listed_by_id = user.id
    business_id = None
    prov = province.strip()
    cty = city.strip()

    if role == "pl":
        if not user.business_id:
            raise HTTPException(status_code=400, detail="PL is not linked to a business")
        business = await db.get(Business, user.business_id)
        if not business or not business.is_active:
            raise HTTPException(status_code=400, detail="Business not found or inactive")
        seller_id = business.owner_user_id
        listed_by_id = user.id
        business_id = business.id
        # Territory defaults from PL assignment (province/city fields — not PostGIS)
        if not prov and user.province:
            prov = user.province
        if not cty and user.city:
            cty = user.city

    if not prov or not cty:
        raise HTTPException(status_code=400, detail="Province and city are required")

    saved = await save_product_images(photos)

    product = Product(
        seller_id=seller_id,
        listed_by_id=listed_by_id,
        business_id=business_id,
        category_id=category.id,
        title=title.strip(),
        description=description.strip(),
        condition=cond,
        brand=brand_val,
        color=color_val,
        parcel_size=parcel_val,
        custom_category=custom,
        price=price_val,
        currency=(currency or "PKR").upper(),
        zone=listing_zone,
        province=prov,
        city=cty,
        shipping_within_city=ship_in,
        shipping_out_of_city=ship_out,
        latitude=latitude if latitude is not None else user.latitude,
        longitude=longitude if longitude is not None else user.longitude,
    )
    db.add(product)
    await db.flush()

    for i, (url, size) in enumerate(saved):
        db.add(
            ProductImage(
                product_id=product.id,
                url=url,
                sort_order=i,
                size_bytes=size,
            )
        )

    # Profile convenience: mirror listing location onto lister if empty
    if not user.province:
        user.province = product.province
    if not user.city:
        user.city = product.city
    if product.latitude is not None:
        user.latitude = product.latitude
    if product.longitude is not None:
        user.longitude = product.longitude

    await db.commit()
    product = await _load_product(db, product.id)
    return _product_out(product)


@router.get("", response_model=ProductListResponse)
async def list_products(
    category_id: int | None = Query(None, alias="categoryId"),
    main_slug: str | None = Query(None, alias="mainSlug"),
    q: str | None = None,
    province: str | None = None,
    city: str | None = None,
    zone: str | None = None,
    business_id: int | None = Query(None, alias="businessId"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Product)
        .options(*_product_load_options())
        .where(Product.is_active.is_(True))
    )

    if category_id is not None:
        cat = await db.get(Category, category_id)
        if not cat:
            raise HTTPException(status_code=404, detail="Category not found")
        if cat.is_virtual_all and cat.parent_id is not None:
            # "View all X" → all non-virtual siblings + their leaves
            sibling_ids = (
                await db.execute(
                    select(Category.id).where(
                        Category.parent_id == cat.parent_id,
                        Category.is_virtual_all.is_(False),
                    )
                )
            ).scalars().all()
            leaf_ids: list[int] = []
            for sid in sibling_ids:
                sib = await db.get(Category, sid)
                if sib:
                    leaf_ids.extend(await _descendant_category_ids(db, sib))
            stmt = stmt.where(Product.category_id.in_(leaf_ids or [-1]))
        else:
            ids = await _descendant_category_ids(db, cat)
            # Products only hang on leaves; exclude the mid/main ids themselves when they have kids
            stmt = stmt.where(Product.category_id.in_(ids))

    if main_slug:
        main = (
            await db.execute(
                select(Category).where(
                    Category.slug == main_slug, Category.parent_id.is_(None)
                )
            )
        ).scalar_one_or_none()
        if main:
            ids = await _descendant_category_ids(db, main)
            stmt = stmt.where(Product.category_id.in_(ids))

    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Product.title.ilike(like),
                Product.description.ilike(like),
                Product.custom_category.ilike(like),
            )
        )
    if province:
        stmt = stmt.where(Product.province.ilike(province.strip()))
    if city:
        stmt = stmt.where(Product.city.ilike(city.strip()))
    if zone:
        stmt = stmt.where(Product.zone == zone.strip().lower())
    if business_id is not None:
        stmt = stmt.where(Product.business_id == business_id)

    stmt = stmt.order_by(Product.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    products = result.scalars().unique().all()
    return ProductListResponse(products=[_product_out(p) for p in products])


@router.get("/nearby", response_model=ProductListResponse)
async def nearby_products(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(50, gt=0, le=500, alias="radiusKm"),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    # Haversine distance in km (optional helper; territory is province/city)
    distance = (
        6371
        * func.acos(
            func.least(
                1.0,
                func.greatest(
                    -1.0,
                    func.cos(func.radians(latitude))
                    * func.cos(func.radians(Product.latitude))
                    * func.cos(func.radians(Product.longitude) - func.radians(longitude))
                    + func.sin(func.radians(latitude))
                    * func.sin(func.radians(Product.latitude)),
                ),
            )
        )
    ).label("distance_km")

    stmt = (
        select(Product, distance)
        .options(*_product_load_options())
        .where(Product.is_active.is_(True))
        .where(Product.latitude.is_not(None))
        .where(Product.longitude.is_not(None))
        .where(distance <= radius_km)
        .order_by(distance)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return ProductListResponse(
        products=[_product_out(p, distance_km=round(float(d), 2)) for p, d in rows]
    )


@router.get("/mine", response_model=ProductListResponse)
async def my_products(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = user.role or "user"
    stmt = select(Product).options(*_product_load_options())

    if role == "pl":
        stmt = stmt.where(Product.listed_by_id == user.id)
    elif role == "business":
        business = (
            await db.execute(
                select(Business).where(Business.owner_user_id == user.id)
            )
        ).scalar_one_or_none()
        if business:
            stmt = stmt.where(Product.business_id == business.id)
        else:
            stmt = stmt.where(Product.seller_id == user.id)
    else:
        stmt = stmt.where(Product.seller_id == user.id)

    result = await db.execute(stmt.order_by(Product.created_at.desc()))
    products = result.scalars().unique().all()
    return ProductListResponse(products=[_product_out(p) for p in products])


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    product = await _load_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _product_out(product)


@router.delete("/{product_id}")
async def deactivate_product(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    role = user.role or "user"
    allowed = (
        product.seller_id == user.id
        or product.listed_by_id == user.id
        or role == "admin"
    )
    if role == "business" and product.business_id:
        business = await db.get(Business, product.business_id)
        if business and business.owner_user_id == user.id:
            allowed = True

    if not allowed:
        raise HTTPException(status_code=403, detail="Not your product")
    product.is_active = False
    await db.commit()
    return {"ok": True}
