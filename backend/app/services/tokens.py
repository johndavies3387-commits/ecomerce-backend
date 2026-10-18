import hashlib
import re
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import RefreshToken, User

settings = get_settings()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        return timedelta(days=7)
    amount = int(match.group(1))
    unit = match.group(2)
    return {
        "s": timedelta(seconds=amount),
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + _parse_duration(settings.jwt_access_expires_in)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "firebaseUid": user.firebase_uid,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_access_secret, algorithm="HS256")


def create_refresh_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + _parse_duration(settings.jwt_refresh_expires_in)
    payload = {
        "sub": str(user.id),
        "type": "refresh",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_refresh_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_access_secret, algorithms=["HS256"])


def decode_refresh_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_refresh_secret, algorithms=["HS256"])


async def issue_token_pair(db: AsyncSession, user: User) -> dict[str, str]:
    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)
    expires_at = datetime.now(timezone.utc) + _parse_duration(
        settings.jwt_refresh_expires_in
    )

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh_token),
            expires_at=expires_at,
        )
    )
    await db.commit()
    return {"access_token": access_token, "refresh_token": refresh_token}


async def rotate_refresh_token(db: AsyncSession, refresh_token: str) -> dict[str, str]:
    try:
        payload = decode_refresh_token(refresh_token)
    except JWTError as exc:
        raise ValueError("Invalid refresh token") from exc

    user_id = int(payload["sub"])
    token_hash = _hash_token(refresh_token)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
            RefreshToken.expires_at > now,
        )
    )
    stored = result.scalar_one_or_none()
    if not stored:
        raise ValueError("Invalid refresh token")

    stored.revoked = True

    user = await db.get(User, user_id)
    if not user:
        raise ValueError("User not found")
    if user.deleted_at is not None or user.is_active is False:
        raise ValueError("Account has been deleted")

    await db.commit()
    return await issue_token_pair(db, user)


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    token_hash = _hash_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored:
        stored.revoked = True
        await db.commit()


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: int) -> None:
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked.is_(False),
        )
    )
    for stored in result.scalars().all():
        stored.revoked = True
    await db.commit()
