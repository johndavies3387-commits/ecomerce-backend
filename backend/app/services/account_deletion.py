"""Self-service account deletion: soft-delete live user + archive for admin."""

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Business, DeletedAccount, Product, User
from app.services import auth as auth_service
from app.services.tokens import revoke_all_refresh_tokens


class DeleteAccountError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def delete_my_account(db: AsyncSession, user: User) -> DeletedAccount:
    if user.deleted_at is not None or user.is_active is False:
        raise DeleteAccountError("Account is already deleted", status_code=400)

    role = user.role or "user"
    if role == "admin":
        raise DeleteAccountError("Admin accounts cannot be self-deleted", status_code=403)

    # Business owners keep the row (FK RESTRICT) but mark business inactive
    if role == "business":
        business = (
            await db.execute(
                select(Business).where(Business.owner_user_id == user.id)
            )
        ).scalar_one_or_none()
        if business:
            business.is_active = False

    now = datetime.now(timezone.utc)
    original_email = user.email
    original_uid = user.firebase_uid

    archive = DeletedAccount(
        user_id=user.id,
        email=original_email,
        display_name=user.display_name,
        phone=user.phone,
        role=role,
        firebase_uid=original_uid,
        province=user.province,
        city=user.city,
        home_zone=user.home_zone,
        seller_plan=user.seller_plan,
        business_id=user.business_id,
        registered_at=user.created_at,
        deleted_at=now,
    )
    db.add(archive)

    # Soft-delete + free email/uid for future re-registration
    user.is_active = False
    user.deleted_at = now
    user.email = f"deleted+{user.id}@deleted.local"
    user.firebase_uid = f"deleted_{user.id}_{original_uid[:32]}"
    user.display_name = user.display_name or "Deleted user"

    await db.execute(
        update(Product)
        .where(Product.seller_id == user.id)
        .values(is_active=False)
    )
    await db.execute(
        update(Product)
        .where(Product.listed_by_id == user.id)
        .values(is_active=False)
    )

    await db.commit()
    await db.refresh(archive)

    await revoke_all_refresh_tokens(db, user.id)
    auth_service.delete_firebase_user(original_uid)

    return archive


async def list_deleted_accounts(db: AsyncSession) -> list[DeletedAccount]:
    result = await db.execute(
        select(DeletedAccount).order_by(DeletedAccount.deleted_at.desc())
    )
    return list(result.scalars().all())


def delete_error_http(exc: DeleteAccountError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)
