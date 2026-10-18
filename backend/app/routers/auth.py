from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.routers.users import user_to_out
from app.schemas import (
    AuthResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    SignupRequest,
    TokenPair,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(body: SignupRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await auth_service.signup(
            db,
            email=body.email.lower().strip(),
            password=body.password,
            display_name=body.display_name.strip() if body.display_name else None,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return AuthResponse(
        user=user_to_out(result["user"]),
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await auth_service.login(
            db,
            email=body.email.lower().strip(),
            password=body.password,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return AuthResponse(
        user=user_to_out(result["user"]),
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        tokens = await auth_service.refresh(db, body.refresh_token)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return TokenPair(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )


@router.post("/logout")
async def logout(body: LogoutRequest, db: AsyncSession = Depends(get_db)):
    await auth_service.logout(db, body.refresh_token)
    return {"ok": True}


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordRequest):
    try:
        await auth_service.send_password_reset_email(body.email)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {
        "ok": True,
        "message": "If an account exists for that email, a reset link has been sent.",
    }


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
):
    try:
        await auth_service.change_password(
            user,
            current_password=body.current_password,
            new_password=body.new_password,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {"ok": True, "message": "Password updated"}


@router.get("/me", response_model=MeResponse)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.sync_email_verified(db, user)
    return MeResponse(user=user_to_out(user))


@router.post("/resend-verification")
async def resend_verification(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        await auth_service.resend_verification(db, user)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {"ok": True, "message": "Verification email sent"}


@router.post("/sync-verification", response_model=MeResponse)
async def sync_verification(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_service.sync_email_verified(db, user)
    return MeResponse(user=user_to_out(user))
