from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import engine, init_db
from app.firebase import init_firebase
from app.routers import auth, categories, chat, complaints, favorites, orders, orgs, products, users
from app.uploads import ensure_upload_dirs

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Pick up .env changes (PUBLIC_BASE_URL / LAN IP) on every process start
    get_settings.cache_clear()
    ensure_upload_dirs()
    init_firebase()
    await init_db()
    yield
    await engine.dispose()


settings = get_settings()

app = FastAPI(title="AR Marketplace API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

upload_path = Path(settings.upload_dir)
upload_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(upload_path)), name="uploads")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(orgs.router)
app.include_router(categories.router)
app.include_router(products.router)
app.include_router(favorites.router)
app.include_router(orders.router)
app.include_router(complaints.router)
app.include_router(chat.router)


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            postgis = None
            try:
                postgis = (await conn.execute(text("SELECT PostGIS_Version()"))).scalar()
            except Exception:
                pass
        return {"ok": True, "db": True, "postgis": postgis}
    except Exception as exc:
        return {"ok": False, "db": False, "error": str(exc)}
