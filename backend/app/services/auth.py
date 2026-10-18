import httpx
from firebase_admin import auth as firebase_auth
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.firebase import is_firebase_ready
from app.models import User
from app.services.tokens import issue_token_pair, revoke_refresh_token, rotate_refresh_token

settings = get_settings()

FIREBASE_SIGNUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp"
FIREBASE_SIGNIN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
)
FIREBASE_OOB_URL = "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode"
FIREBASE_UPDATE_URL = "https://identitytoolkit.googleapis.com/v1/accounts:update"


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _ensure_firebase() -> None:
    if not is_firebase_ready():
        raise AuthError(
            "Firebase is not configured. Paste serviceAccount.json into the backend folder.",
            status_code=503,
        )
    if not settings.firebase_api_key:
        raise AuthError("FIREBASE_API_KEY is missing in .env", status_code=503)


async def _firebase_password_auth(url: str, email: str, password: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{url}?key={settings.firebase_api_key}",
            json={"email": email, "password": password, "returnSecureToken": True},
        )
    data = response.json()
    if response.is_success:
        return data

    code = data.get("error", {}).get("message", "")
    if code == "EMAIL_EXISTS":
        raise AuthError("Email already registered", status_code=401)
    if code in {
        "EMAIL_NOT_FOUND",
        "INVALID_PASSWORD",
        "INVALID_LOGIN_CREDENTIALS",
    }:
        raise AuthError("Invalid email or password", status_code=401)
    raise AuthError(code or "Firebase authentication failed", status_code=401)


async def send_verification_email(id_token: str) -> None:
    """Ask Firebase to email a verification link (uses console email template)."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{FIREBASE_OOB_URL}?key={settings.firebase_api_key}",
            json={"requestType": "VERIFY_EMAIL", "idToken": id_token},
        )
    data = response.json()
    if not response.is_success:
        code = data.get("error", {}).get("message", "")
        raise AuthError(
            code or "Failed to send verification email",
            status_code=400,
        )


def create_firebase_user(
    *,
    email: str,
    password: str,
    display_name: str | None = None,
    email_verified: bool = True,
) -> str:
    """Create a Firebase Auth user via Admin SDK. Returns firebase uid."""
    _ensure_firebase()
    if len(password) < 6:
        raise AuthError("Password must be at least 6 characters", status_code=400)
    try:
        record = firebase_auth.create_user(
            email=email.lower().strip(),
            password=password,
            display_name=display_name,
            email_verified=email_verified,
        )
        return record.uid
    except firebase_auth.EmailAlreadyExistsError as exc:
        raise AuthError("Email already registered", status_code=400) from exc
    except Exception as exc:  # noqa: BLE001
        raise AuthError(str(exc) or "Could not create Firebase user", status_code=400) from exc


def delete_firebase_user(firebase_uid: str) -> None:
    try:
        firebase_auth.delete_user(firebase_uid)
    except Exception:
        pass


async def send_password_reset_email(email: str) -> None:
    """Ask Firebase to email a password-reset link. Always succeeds from the caller's
    perspective for unknown emails (no account enumeration)."""
    _ensure_firebase()
    normalized = email.lower().strip()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{FIREBASE_OOB_URL}?key={settings.firebase_api_key}",
            json={"requestType": "PASSWORD_RESET", "email": normalized},
        )
    data = response.json()
    if response.is_success:
        return

    code = data.get("error", {}).get("message", "")
    # Hide "user not found" — still tell the client a generic success message.
    if code in {"EMAIL_NOT_FOUND", "INVALID_EMAIL"}:
        return
    raise AuthError(code or "Failed to send reset email", status_code=400)


async def change_password(user: User, *, current_password: str, new_password: str) -> None:
    """Re-authenticate with current password, then update password in Firebase."""
    _ensure_firebase()
    if len(new_password) < 6:
        raise AuthError("New password must be at least 6 characters", status_code=400)
    if current_password == new_password:
        raise AuthError("New password must be different from the current one", status_code=400)

    # Prove the user knows the current password
    try:
        firebase_user = await _firebase_password_auth(
            FIREBASE_SIGNIN_URL, user.email, current_password
        )
    except AuthError as exc:
        if exc.status_code == 401:
            raise AuthError("Current password is incorrect", status_code=401) from exc
        raise
    id_token = firebase_user["idToken"]

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{FIREBASE_UPDATE_URL}?key={settings.firebase_api_key}",
            json={
                "idToken": id_token,
                "password": new_password,
                "returnSecureToken": True,
            },
        )
    data = response.json()
    if not response.is_success:
        code = data.get("error", {}).get("message", "")
        if code == "WEAK_PASSWORD : Password should be at least 6 characters":
            raise AuthError("New password must be at least 6 characters", status_code=400)
        raise AuthError(code or "Could not update password", status_code=400)


def _maybe_bootstrap_admin(user: User, email: str) -> None:
    bootstrap = (settings.admin_bootstrap_email or "").strip().lower()
    if bootstrap and email.lower() == bootstrap and (user.role or "user") != "admin":
        user.role = "admin"


async def _upsert_user(
    db: AsyncSession,
    *,
    firebase_uid: str,
    email: str,
    display_name: str | None,
    email_verified: bool = False,
) -> User:
    result = await db.execute(
        select(User).where(or_(User.firebase_uid == firebase_uid, User.email == email))
    )
    user = result.scalar_one_or_none()

    if user:
        if user.deleted_at is not None or user.is_active is False:
            raise AuthError(
                "This account has been deleted. Contact support if you need help.",
                status_code=403,
            )
        user.email = email
        user.firebase_uid = firebase_uid
        user.email_verified = email_verified
        if display_name:
            user.display_name = display_name
    else:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            email_verified=email_verified,
        )
        db.add(user)

    _maybe_bootstrap_admin(user, email)
    await db.commit()
    await db.refresh(user)
    return user


async def sync_email_verified(db: AsyncSession, user: User) -> User:
    """Pull latest emailVerified flag from Firebase into Postgres."""
    try:
        record = firebase_auth.get_user(user.firebase_uid)
        verified = bool(record.email_verified)
        if user.email_verified != verified:
            user.email_verified = verified
            await db.commit()
            await db.refresh(user)
    except Exception:
        pass
    return user


async def signup(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
) -> dict:
    _ensure_firebase()
    firebase_user = await _firebase_password_auth(FIREBASE_SIGNUP_URL, email, password)

    if display_name:
        try:
            firebase_auth.update_user(firebase_user["localId"], display_name=display_name)
        except Exception:
            pass

    # Firebase sends the confirmation email via its template
    try:
        await send_verification_email(firebase_user["idToken"])
    except AuthError:
        # User is created — verification can be resent from the app
        pass

    user = await _upsert_user(
        db,
        firebase_uid=firebase_user["localId"],
        email=email,
        display_name=display_name,
        email_verified=False,
    )
    tokens = await issue_token_pair(db, user)
    return {"user": user, **tokens}


async def login(db: AsyncSession, *, email: str, password: str) -> dict:
    _ensure_firebase()
    firebase_user = await _firebase_password_auth(FIREBASE_SIGNIN_URL, email, password)

    display_name = None
    email_verified = False
    try:
        record = firebase_auth.get_user(firebase_user["localId"])
        display_name = record.display_name
        email_verified = bool(record.email_verified)
    except Exception:
        pass

    user = await _upsert_user(
        db,
        firebase_uid=firebase_user["localId"],
        email=email,
        display_name=display_name,
        email_verified=email_verified,
    )
    tokens = await issue_token_pair(db, user)
    return {"user": user, **tokens}


async def _id_token_for_user(firebase_uid: str) -> str:
    """Mint a short-lived Firebase idToken via custom token (no password needed)."""
    custom_token = firebase_auth.create_custom_token(firebase_uid)
    if isinstance(custom_token, bytes):
        custom_token = custom_token.decode("utf-8")

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
            f"?key={settings.firebase_api_key}",
            json={"token": custom_token, "returnSecureToken": True},
        )
    data = response.json()
    if not response.is_success:
        raise AuthError(
            data.get("error", {}).get("message") or "Could not create Firebase session",
            status_code=400,
        )
    return data["idToken"]


async def resend_verification(db: AsyncSession, user: User) -> None:
    """Resend Firebase verification email for the logged-in user."""
    _ensure_firebase()
    user = await sync_email_verified(db, user)
    if user.email_verified:
        raise AuthError("Email is already verified", status_code=400)

    id_token = await _id_token_for_user(user.firebase_uid)
    await send_verification_email(id_token)


async def refresh(db: AsyncSession, refresh_token: str) -> dict[str, str]:
    try:
        return await rotate_refresh_token(db, refresh_token)
    except ValueError as exc:
        raise AuthError(str(exc), status_code=401) from exc


async def logout(db: AsyncSession, refresh_token: str | None) -> None:
    if refresh_token:
        await revoke_refresh_token(db, refresh_token)
