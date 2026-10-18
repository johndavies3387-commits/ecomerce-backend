from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    port: int = 4000
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ar_marketplace"

    jwt_access_secret: str = "dev-access-secret-change-in-production-32c"
    jwt_refresh_secret: str = "dev-refresh-secret-change-in-production-32c"
    jwt_access_expires_in: str = "15m"
    jwt_refresh_expires_in: str = "7d"

    firebase_api_key: str = ""
    google_application_credentials: str = "./serviceAccount.json"

    upload_dir: str = str(BASE_DIR / "uploads")
    public_base_url: str = "http://localhost:4000"
    max_product_photos: int = 5
    max_product_photos_bytes: int = 2 * 1024 * 1024  # 2 MB total

    # Promote this email to admin on login/signup (leave empty to skip)
    admin_bootstrap_email: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
