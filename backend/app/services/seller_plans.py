"""Seller plan activation and listing-quota enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, User
from app.plans import (
    DELETED_FREES_SLOT,
    PLAN_ORDER,
    PLANS,
    QUOTA_RESET,
    ZONE_LABELS,
    ZONES,
    encode_allowed_zones,
    normalize_zone,
    parse_allowed_zones,
    plan_config,
    registration_fee,
)


class PlanError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def resolve_quota_user(db: AsyncSession, actor: User) -> User:
    """Peer sellers use their own plan; PLs use the business owner's plan."""
    role = actor.role or "user"
    if role == "pl":
        if not actor.business_id:
            raise PlanError("PL is not linked to a business", status_code=400)
        from app.models import Business

        business = await db.get(Business, actor.business_id)
        if not business:
            raise PlanError("Business not found", status_code=400)
        owner = await db.get(User, business.owner_user_id)
        if not owner:
            raise PlanError("Business owner not found", status_code=400)
        return owner
    return actor


async def _main_category_id(db: AsyncSession, category_id: int) -> int:
    cat = await db.get(Category, category_id)
    if not cat:
        raise PlanError("Category not found", status_code=400)
    while cat.parent_id is not None:
        parent = await db.get(Category, cat.parent_id)
        if not parent:
            break
        cat = parent
    return cat.id


async def lifetime_listing_counts(db: AsyncSession, seller_id: int, home_zone: str) -> tuple[int, int]:
    """Count ALL listings ever created (active + inactive). Deleted does not free a slot."""
    own = (
        await db.execute(
            select(func.count(Product.id)).where(
                Product.seller_id == seller_id,
                Product.zone == home_zone,
            )
        )
    ).scalar_one()
    other = (
        await db.execute(
            select(func.count(Product.id)).where(
                Product.seller_id == seller_id,
                Product.zone != home_zone,
            )
        )
    ).scalar_one()
    return int(own or 0), int(other or 0)


def plan_summary(user: User, *, own_used: int = 0, other_used: int = 0) -> dict:
    plan = (user.seller_plan or "").lower() or None
    cfg = plan_config(plan)
    home = normalize_zone(user.home_zone)
    allowed = parse_allowed_zones(user.allowed_zones)
    if home and home not in allowed:
        allowed = [home, *[z for z in allowed if z != home]]

    return {
        "plan": plan,
        "homeZone": home,
        "allowedZones": allowed,
        "sellerMainCategoryId": user.seller_main_category_id,
        "multiCategory": bool(cfg["multi_category"]) if cfg else False,
        "ownZoneUsed": own_used,
        "ownZoneLimit": int(cfg["own_zone_listings"]) if cfg else 0,
        "otherZoneUsed": other_used,
        "otherZoneLimit": int(cfg["other_zone_listings"]) if cfg else 0,
        "quotaReset": QUOTA_RESET,
        "deletedFreesSlot": DELETED_FREES_SLOT,
        "zones": [{"id": z, "label": ZONE_LABELS.get(z, z.title())} for z in ZONES],
        "plans": {
            key: {
                "ownZoneListings": val["own_zone_listings"],
                "otherZoneListings": val["other_zone_listings"],
                "maxExtraZones": val["max_extra_zones"],
                "multiCategory": val["multi_category"],
                "registrationFee": val["registration_fee"],
            }
            for key, val in PLANS.items()
        },
    }


async def get_plan_summary(db: AsyncSession, actor: User) -> dict:
    quota_user = await resolve_quota_user(db, actor)
    home = normalize_zone(quota_user.home_zone) or "pakistan"
    own_used, other_used = (0, 0)
    if quota_user.seller_plan:
        own_used, other_used = await lifetime_listing_counts(db, quota_user.id, home)
    summary = plan_summary(quota_user, own_used=own_used, other_used=other_used)
    # Surface whether the actor is using a business owner's plan
    summary["quotaOwnerUserId"] = quota_user.id
    summary["quotaOwnerEmail"] = quota_user.email
    return summary


def _assert_upgrade_path(current: str | None, target: str) -> None:
    if target not in PLANS:
        raise PlanError("Unknown plan")
    if not current:
        if target != "basic" and target != "pro":
            # private users start at basic; companies may go pro (checked separately)
            if target == "smart":
                raise PlanError("Create a Basic account before upgrading to Smart")
        return
    if PLAN_ORDER.get(target, 0) < PLAN_ORDER.get(current, 0):
        raise PlanError("Cannot downgrade plan")
    if current == "basic" and target == "pro":
        # allow skip smart
        return
    if current == target:
        raise PlanError("Already on this plan")


async def activate_or_upgrade_plan(
    db: AsyncSession,
    user: User,
    *,
    plan: str,
    home_zone: str,
    extra_zones: list[str] | None,
    accepted_terms: bool,
    seller_type: str = "private",
) -> User:
    if not accepted_terms:
        raise PlanError("You must accept the Terms and Conditions")

    plan = plan.strip().lower()
    home = normalize_zone(home_zone)
    if not home:
        raise PlanError(f"homeZone must be one of: {', '.join(ZONES)}")

    cfg = plan_config(plan)
    if not cfg:
        raise PlanError("Unknown plan")

    current = (user.seller_plan or "").lower() or None
    role = user.role or "user"

    if plan == "smart" and current is None:
        raise PlanError("Create a Basic account before upgrading to Smart")

    if plan == "pro" and seller_type == "private" and role == "user":
        # Private users: after 3 months on platform (or already selling)
        created = user.created_at or datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created < timedelta(days=90) and current is None:
            raise PlanError(
                "Private users can apply for Pro after 3 months, or start with Basic",
                status_code=400,
            )

    if role == "business" and plan in {"basic", "smart", "pro"}:
        pass  # companies may take any plan including direct Pro
    elif current:
        _assert_upgrade_path(current, plan)
    elif plan == "smart":
        raise PlanError("Create a Basic account before upgrading to Smart")

    extras = [normalize_zone(z) for z in (extra_zones or [])]
    extras = [z for z in extras if z and z != home]
    max_extra = int(cfg["max_extra_zones"])
    if len(extras) > max_extra:
        raise PlanError(f"This plan allows at most {max_extra} additional zone(s)")

    allowed = [home, *extras]
    # Pro may select all zones
    if plan == "pro" and max_extra >= len(ZONES) - 1 and len(extras) >= len(ZONES) - 1:
        allowed = list(ZONES)

    user.seller_plan = plan
    user.home_zone = home
    user.allowed_zones = encode_allowed_zones(allowed)
    if not user.plan_activated_at:
        user.plan_activated_at = datetime.now(timezone.utc)

    # Fee is recorded conceptually; payment gateway can be wired later
    _ = registration_fee(plan, home)

    await db.commit()
    await db.refresh(user)
    return user


async def assert_can_create_listing(
    db: AsyncSession,
    actor: User,
    *,
    zone: str,
    category_id: int,
) -> tuple[User, str]:
    """Raise HTTPException-friendly PlanError if listing is not allowed. Returns (quota_user, zone)."""
    quota_user = await resolve_quota_user(db, actor)
    plan = (quota_user.seller_plan or "").lower() or None
    if not plan:
        raise PlanError(
            "Activate a seller plan (Basic, Smart, or Pro) before listing",
            status_code=403,
        )

    cfg = plan_config(plan)
    if not cfg:
        raise PlanError("Invalid seller plan", status_code=400)

    z = normalize_zone(zone)
    if not z:
        raise PlanError(f"zone must be one of: {', '.join(ZONES)}")

    allowed = parse_allowed_zones(quota_user.allowed_zones)
    home = normalize_zone(quota_user.home_zone)
    if home and home not in allowed:
        allowed.append(home)
    if z not in allowed:
        raise PlanError(
            f"Your plan cannot list in {z}. Allowed zones: {', '.join(allowed) or 'none'}",
            status_code=403,
        )

    main_id = await _main_category_id(db, category_id)
    if not cfg["multi_category"]:
        if quota_user.seller_main_category_id is None:
            quota_user.seller_main_category_id = main_id
        elif quota_user.seller_main_category_id != main_id:
            raise PlanError(
                "Basic plan allows listings in a single category only",
                status_code=403,
            )

    own_used, other_used = await lifetime_listing_counts(
        db, quota_user.id, home or z
    )
    is_own = z == home
    if is_own:
        limit = int(cfg["own_zone_listings"])
        if own_used >= limit:
            raise PlanError(
                f"Own-zone free listing limit reached ({own_used}/{limit}). "
                "Deleted listings do not free a slot. Upgrade your plan or pay for extra listings.",
                status_code=403,
            )
    else:
        limit = int(cfg["other_zone_listings"])
        if limit <= 0:
            raise PlanError("Your plan does not include listings in other zones", status_code=403)
        if other_used >= limit:
            raise PlanError(
                f"Other-zone free listing limit reached ({other_used}/{limit}). "
                "Deleted listings do not free a slot. Upgrade your plan or pay for extra listings.",
                status_code=403,
            )

    return quota_user, z


def plan_error_http(exc: PlanError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)
