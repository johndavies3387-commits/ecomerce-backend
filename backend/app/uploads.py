import uuid
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from app.config import get_settings

settings = get_settings()
ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def ensure_upload_dirs() -> Path:
    root = Path(settings.upload_dir)
    products = root / "products"
    avatars = root / "avatars"
    products.mkdir(parents=True, exist_ok=True)
    avatars.mkdir(parents=True, exist_ok=True)
    return root


async def save_product_images(files: list[UploadFile]) -> list[tuple[str, int]]:
    """Save 1–5 images. Total size must be ≤ 2 MB. Returns [(public_url, size_bytes)]."""
    if not files:
        raise HTTPException(status_code=400, detail="At least 1 photo is required")
    if len(files) > settings.max_product_photos:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {settings.max_product_photos} photos allowed",
        )

    ensure_upload_dirs()
    saved: list[tuple[str, int]] = []
    total = 0

    for file in files:
        content_type = (file.content_type or "").lower()
        if content_type not in ALLOWED_TYPES:
            raise HTTPException(status_code=400, detail="Photos must be JPEG, PNG, or WebP")

        data = await file.read()
        size = len(data)
        total += size
        if total > settings.max_product_photos_bytes:
            raise HTTPException(
                status_code=400,
                detail="Photos together must be 2 MB or less",
            )

        ext = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }[content_type]
        name = f"{uuid.uuid4().hex}{ext}"
        path = Path(settings.upload_dir) / "products" / name
        async with aiofiles.open(path, "wb") as out:
            await out.write(data)

        # Store path only — host is applied when serving (survives LAN IP changes)
        saved.append((f"/uploads/products/{name}", size))

    return saved


def public_media_url(stored: str | None) -> str | None:
    """Turn a relative or absolute media path into a URL using current PUBLIC_BASE_URL."""
    if not stored:
        return None
    from urllib.parse import urlparse

    base = get_settings().public_base_url.rstrip("/")
    if stored.startswith("/"):
        return f"{base}{stored}"
    path = urlparse(stored).path or stored
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"


async def save_avatar(file: UploadFile) -> str:
    ensure_upload_dirs()
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Avatar must be JPEG, PNG, or WebP")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Avatar must be 2 MB or less")

    ext = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }[content_type]
    name = f"{uuid.uuid4().hex}{ext}"
    path = Path(settings.upload_dir) / "avatars" / name
    async with aiofiles.open(path, "wb") as out:
        await out.write(data)
    return f"/uploads/avatars/{name}"
