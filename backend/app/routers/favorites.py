from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import Favorite, Product, User
from app.routers.products import _product_load_options, _product_out
from app.schemas import ProductListResponse

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.get("", response_model=ProductListResponse)
async def list_favorites(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Product)
        .join(Favorite, Favorite.product_id == Product.id)
        .where(Favorite.user_id == user.id, Product.is_active.is_(True))
        .options(*_product_load_options())
        .order_by(Favorite.created_at.desc())
    )
    products = result.scalars().unique().all()
    return ProductListResponse(products=[_product_out(p) for p in products])


@router.get("/ids")
async def favorite_ids(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(select(Favorite.product_id).where(Favorite.user_id == user.id))
    ).scalars().all()
    return {"productIds": list(rows)}


@router.post("/{product_id}")
async def add_favorite(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    product = await db.get(Product, product_id)
    if not product or not product.is_active:
        raise HTTPException(status_code=404, detail="Product not found")
    existing = (
        await db.execute(
            select(Favorite).where(
                Favorite.user_id == user.id, Favorite.product_id == product_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        return {"ok": True, "favorited": True}
    db.add(Favorite(user_id=user.id, product_id=product_id))
    await db.commit()
    return {"ok": True, "favorited": True}


@router.delete("/{product_id}")
async def remove_favorite(
    product_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(Favorite).where(
            Favorite.user_id == user.id, Favorite.product_id == product_id
        )
    )
    await db.commit()
    return {"ok": True, "favorited": False}
