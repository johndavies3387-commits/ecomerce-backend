from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_roles
from app.models import User
from app.plans import parse_allowed_zones
from app.schemas import (
    ActivateSellerPlanRequest,
    DeleteAccountResponse,
    DeletedAccountListResponse,
    DeletedAccountOut,
    LocationUpdate,
    MeResponse,
    ProfileUpdate,
    SellerPlanSummaryResponse,
    UserOut,
)
from app.services import account_deletion as deletion_service
from app.services import auth as auth_service
from app.services import seller_plans as plan_service
from app.uploads import public_media_url, save_avatar

router = APIRouter(prefix="/users", tags=["users"])


def user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        bio=user.bio,
        phone=user.phone,
        gender=user.gender,
        birth_date=user.birth_date,
        avatar_url=public_media_url(user.avatar_url) or user.avatar_url,
        province=user.province,
        city=user.city,
        firebase_uid=user.firebase_uid,
        email_verified=bool(user.email_verified),
        role=user.role or "user",
        business_id=user.business_id,
        latitude=user.latitude,
        longitude=user.longitude,
        seller_plan=user.seller_plan,
        home_zone=user.home_zone,
        allowed_zones=parse_allowed_zones(user.allowed_zones),
        seller_main_category_id=user.seller_main_category_id,
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.sync_email_verified(db, user)
    return MeResponse(user=user_to_out(user))


@router.patch("/me", response_model=MeResponse)
async def update_profile(
    body: ProfileUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return MeResponse(user=user_to_out(user))


@router.put("/me/location", response_model=MeResponse)
async def update_my_location(
    body: LocationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.latitude = body.latitude
    user.longitude = body.longitude
    await db.commit()
    await db.refresh(user)
    return MeResponse(user=user_to_out(user))


@router.post("/me/avatar", response_model=MeResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.avatar_url = await save_avatar(file)
    await db.commit()
    await db.refresh(user)
    return MeResponse(user=user_to_out(user))


@router.delete("/me", response_model=DeleteAccountResponse)
async def delete_my_account(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await deletion_service.delete_my_account(db, user)
    except deletion_service.DeleteAccountError as exc:
        raise deletion_service.delete_error_http(exc) from exc
    return DeleteAccountResponse(
        ok=True,
        message="Your account has been deleted. A record was kept for admin review.",
    )


@router.get("/deleted", response_model=DeletedAccountListResponse)
async def list_deleted_accounts(
    _: User = Depends(require_roles("admin")),
    db: AsyncSession = Depends(get_db),
):
    rows = await deletion_service.list_deleted_accounts(db)
    return DeletedAccountListResponse(
        accounts=[DeletedAccountOut.model_validate(r) for r in rows]
    )


@router.get("/me/seller-plan", response_model=SellerPlanSummaryResponse)
async def get_seller_plan(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    summary = await plan_service.get_plan_summary(db, user)
    return SellerPlanSummaryResponse.model_validate(summary)


@router.post("/me/seller-plan", response_model=MeResponse)
async def activate_seller_plan(
    body: ActivateSellerPlanRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    role = user.role or "user"
    if role == "admin":
        raise HTTPException(status_code=403, detail="Admin cannot activate a seller plan")
    if role == "pl":
        raise HTTPException(
            status_code=403,
            detail="PL uses the business owner plan — ask the business to upgrade",
        )
    try:
        user = await plan_service.activate_or_upgrade_plan(
            db,
            user,
            plan=body.plan,
            home_zone=body.home_zone,
            extra_zones=list(body.extra_zones or []),
            accepted_terms=body.accepted_terms,
            seller_type=body.seller_type,
        )
    except plan_service.PlanError as exc:
        raise plan_service.plan_error_http(exc) from exc
    return MeResponse(user=user_to_out(user))
