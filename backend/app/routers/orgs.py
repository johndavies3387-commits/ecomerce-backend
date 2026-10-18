from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_roles
from app.models import Business, User
from app.schemas import (
    BusinessListResponse,
    BusinessOut,
    CreateBusinessRequest,
    CreatePLRequest,
    PLListResponse,
    PLOut,
)
from app.plans import ZONES, encode_allowed_zones, normalize_zone
from app.services import auth as auth_service
from datetime import datetime, timezone

router = APIRouter(prefix="/orgs", tags=["orgs"])


def _business_out(
    business: Business,
    *,
    owner: User | None = None,
    pl_count: int = 0,
) -> BusinessOut:
    owner = owner or business.owner
    return BusinessOut(
        id=business.id,
        name=business.name,
        owner_user_id=business.owner_user_id,
        owner_email=owner.email if owner else None,
        owner_name=(owner.display_name or owner.email) if owner else None,
        province=business.province,
        city=business.city,
        phone=business.phone,
        is_active=business.is_active,
        pl_count=pl_count,
    )


def _pl_out(user: User) -> PLOut:
    return PLOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        province=user.province,
        city=user.city,
        phone=user.phone,
        business_id=user.business_id,
        role=user.role or "pl",
    )


async def _pl_counts(db: AsyncSession, business_ids: list[int]) -> dict[int, int]:
    if not business_ids:
        return {}
    rows = (
        await db.execute(
            select(User.business_id, func.count(User.id))
            .where(User.business_id.in_(business_ids), User.role == "pl")
            .group_by(User.business_id)
        )
    ).all()
    return {bid: int(count) for bid, count in rows}


@router.post("/businesses", response_model=BusinessOut, status_code=201)
async def create_business(
    body: CreateBusinessRequest,
    admin: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates a business account (Firebase + DB) and hands credentials to the owner."""
    email = body.owner_email.lower().strip()
    existing_user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    display_name = (body.owner_display_name or "").strip() or body.name.strip()
    province = (body.province or "").strip() or None
    city = (body.city or "").strip() or None
    phone = (body.phone or "").strip() or None
    home_zone = normalize_zone(body.home_zone) or "pakistan"
    seller_plan = (body.seller_plan or "pro").strip().lower()
    if seller_plan not in {"basic", "smart", "pro"}:
        raise HTTPException(status_code=400, detail="Invalid seller plan")
    # Companies created by admin get all launch zones on Pro by default
    allowed = list(ZONES) if seller_plan == "pro" else [home_zone]

    try:
        firebase_uid = auth_service.create_firebase_user(
            email=email,
            password=body.owner_password,
            display_name=display_name,
            email_verified=True,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    try:
        owner = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            phone=phone,
            province=province,
            city=city,
            email_verified=True,
            role="business",
            seller_plan=seller_plan,
            home_zone=home_zone,
            allowed_zones=encode_allowed_zones(allowed),
            plan_activated_at=datetime.now(timezone.utc),
        )
        db.add(owner)
        await db.flush()

        business = Business(
            name=body.name.strip(),
            owner_user_id=owner.id,
            created_by_admin_id=admin.id,
            province=province,
            city=city,
            phone=phone,
        )
        db.add(business)
        await db.flush()

        owner.business_id = business.id
        await db.commit()
        await db.refresh(business)
    except Exception:
        await db.rollback()
        auth_service.delete_firebase_user(firebase_uid)
        raise

    return _business_out(business, owner=owner, pl_count=0)


@router.get("/businesses", response_model=BusinessListResponse)
async def list_businesses(
    user: User = Depends(require_roles("admin", "business")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Business).options(selectinload(Business.owner)).where(
        Business.is_active.is_(True)
    )
    if (user.role or "user") == "business":
        stmt = stmt.where(Business.owner_user_id == user.id)

    businesses = (await db.execute(stmt.order_by(Business.created_at.desc()))).scalars().all()
    counts = await _pl_counts(db, [b.id for b in businesses])
    return BusinessListResponse(
        businesses=[
            _business_out(b, pl_count=counts.get(b.id, 0)) for b in businesses
        ]
    )


@router.get("/my-business", response_model=BusinessOut)
async def my_business(
    user: User = Depends(require_roles("business", "pl")),
    db: AsyncSession = Depends(get_db),
):
    business_id = user.business_id
    if (user.role or "user") == "business" and not business_id:
        owned = (
            await db.execute(
                select(Business)
                .options(selectinload(Business.owner))
                .where(Business.owner_user_id == user.id)
            )
        ).scalar_one_or_none()
        if not owned:
            raise HTTPException(status_code=404, detail="No business linked")
        counts = await _pl_counts(db, [owned.id])
        return _business_out(owned, pl_count=counts.get(owned.id, 0))

    if not business_id:
        raise HTTPException(status_code=404, detail="No business linked")

    business = (
        await db.execute(
            select(Business)
            .options(selectinload(Business.owner))
            .where(Business.id == business_id)
        )
    ).scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Business not found")
    counts = await _pl_counts(db, [business.id])
    return _business_out(business, pl_count=counts.get(business.id, 0))


@router.post("/pls", response_model=PLOut, status_code=201)
async def create_pl(
    body: CreatePLRequest,
    business_user: User = Depends(require_roles("business")),
    db: AsyncSession = Depends(get_db),
):
    """Business creates a PL account (Firebase + DB) and hands credentials to the PL."""
    business = (
        await db.execute(
            select(Business).where(Business.owner_user_id == business_user.id)
        )
    ).scalar_one_or_none()
    if not business or not business.is_active:
        raise HTTPException(status_code=404, detail="Business not found")

    email = body.email.lower().strip()
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    display_name = (body.display_name or "").strip() or None
    province = body.province.strip()
    city = body.city.strip()
    phone = (body.phone or "").strip() or None

    try:
        firebase_uid = auth_service.create_firebase_user(
            email=email,
            password=body.password,
            display_name=display_name,
            email_verified=True,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    try:
        pl = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            phone=phone,
            province=province,
            city=city,
            email_verified=True,
            role="pl",
            business_id=business.id,
        )
        db.add(pl)
        await db.commit()
        await db.refresh(pl)
    except Exception:
        await db.rollback()
        auth_service.delete_firebase_user(firebase_uid)
        raise

    return _pl_out(pl)


@router.get("/pls", response_model=PLListResponse)
async def list_pls(
    user: User = Depends(require_roles("admin", "business")),
    db: AsyncSession = Depends(get_db),
    business_id: int | None = Query(None, alias="businessId"),
):
    role = user.role or "user"
    if role == "business":
        business = (
            await db.execute(
                select(Business).where(Business.owner_user_id == user.id)
            )
        ).scalar_one_or_none()
        if not business:
            raise HTTPException(status_code=404, detail="Business not found")
        target_id = business.id
    else:
        if business_id is None:
            raise HTTPException(
                status_code=400, detail="businessId query param required for admin"
            )
        target_id = business_id

    pls = (
        await db.execute(
            select(User)
            .where(User.business_id == target_id, User.role == "pl")
            .order_by(User.created_at.desc())
        )
    ).scalars().all()
    return PLListResponse(pls=[_pl_out(u) for u in pls])
