from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from app.config import BASE_DIR, get_settings

_initialized = False


def init_firebase() -> None:
    global _initialized
    if _initialized or firebase_admin._apps:
        _initialized = True
        return

    settings = get_settings()
    cred_path = Path(settings.google_application_credentials)
    if not cred_path.is_absolute():
        cred_path = BASE_DIR / cred_path

    if not cred_path.exists():
        print(
            "[firebase] serviceAccount.json not found. "
            "Paste your Firebase service account JSON as backend/serviceAccount.json"
        )
        return

    cred = credentials.Certificate(str(cred_path))
    firebase_admin.initialize_app(cred)
    _initialized = True


def is_firebase_ready() -> bool:
    return bool(firebase_admin._apps)
