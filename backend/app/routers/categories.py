from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database import get_db
from app.models import Category
from app.schemas import CategoryListResponse, CategoryOut

router = APIRouter(prefix="/categories", tags=["categories"])

def _cat_out(cat: Category) -> CategoryOut:
    return CategoryOut(
        id=cat.id,
        name=cat.name,
        slug=cat.slug,
        is_virtual_all=cat.is_virtual_all,
        children=[
            _cat_out(c)
            for c in sorted(cat.children or [], key=lambda x: x.sort_order)
        ],
    )

@router.get("", response_model=CategoryListResponse)
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Category)
        .where(Category.parent_id.is_(None))
        # Chain selectinload to deeply fetch 3 full tiers (Main -> Sub -> Leaf) without triggering N+1
        .options(
            # main → mid → leaf → (empty) so leaf.children never lazy-loads
            selectinload(Category.children)
            .selectinload(Category.children)
            .selectinload(Category.children)
        )
        .order_by(Category.sort_order)
    )
    mains = result.scalars().unique().all()
    return CategoryListResponse(categories=[_cat_out(c) for c in mains])
