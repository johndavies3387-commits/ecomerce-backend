from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

db_url = settings.database_url
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

POSTGIS_ENABLED = False


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    global POSTGIS_ENABLED
    from sqlalchemy import text

    from app import models  # noqa: F401
    from app.seed import seed_categories

    # Probe PostGIS in its own connection so failure doesn't abort schema setup
    async with engine.connect() as probe:
        try:
            await probe.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await probe.commit()
            POSTGIS_ENABLED = True
            print("[db] PostGIS enabled")
        except Exception as exc:
            await probe.rollback()
            POSTGIS_ENABLED = False
            print(f"[db] PostGIS unavailable ({exc.__class__.__name__}); using lat/lng columns")

    async with engine.begin() as conn:
        # Recreate marketplace catalog tables if schema is outdated / partially created
        has_category = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='products' AND column_name='category_id'"
                )
            )
        ).first()
        cats_ok = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='categories'"
                )
            )
        ).first()
        if not has_category or not cats_ok:
            print("[db] resetting catalog tables (categories / products)")
            await conn.execute(text("DROP TABLE IF EXISTS product_images CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS products CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS categories CASCADE"))
            # Clean orphan composite types from aborted CREATE TABLE attempts
            await conn.execute(text("DROP TYPE IF EXISTS product_images CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS products CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS categories CASCADE"))

        await conn.run_sync(Base.metadata.create_all)

        alters = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS birth_date DATE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS province VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS city VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS seller_plan VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS home_zone VARCHAR(32)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS allowed_zones VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS seller_main_category_id INTEGER",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_activated_at TIMESTAMPTZ",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
            "ALTER TABLE chat_notifications ADD COLUMN IF NOT EXISTS kind VARCHAR(40) NOT NULL DEFAULT 'chat'",
            "ALTER TABLE chat_notifications ADD COLUMN IF NOT EXISTS product_id INTEGER",
            "ALTER TABLE chat_notifications ADD COLUMN IF NOT EXISTS order_id INTEGER",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS listed_by_id INTEGER",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS business_id INTEGER",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS condition VARCHAR(32) NOT NULL DEFAULT 'good'",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS brand VARCHAR(120)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS color VARCHAR(64)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS parcel_size VARCHAR(16)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS custom_category VARCHAR(120)",
            "ALTER TABLE products ADD COLUMN IF NOT EXISTS zone VARCHAR(32) NOT NULL DEFAULT 'pakistan'",
            "ALTER TABLE complaints ADD COLUMN IF NOT EXISTS conversation_id INTEGER",
        ]
        for stmt in alters:
            await conn.execute(text(stmt))

        # Soft FKs for org hierarchy (safe if already present)
        fk_alters = [
            """
            DO $$ BEGIN
              ALTER TABLE users
                ADD CONSTRAINT fk_users_business_id
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """,
            """
            DO $$ BEGIN
              ALTER TABLE products
                ADD CONSTRAINT fk_products_listed_by_id
                FOREIGN KEY (listed_by_id) REFERENCES users(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """,
            """
            DO $$ BEGIN
              ALTER TABLE products
                ADD CONSTRAINT fk_products_business_id
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """,
            """
            DO $$ BEGIN
              ALTER TABLE complaints
                ADD CONSTRAINT fk_complaints_conversation_id
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE SET NULL;
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """,
        ]
        for stmt in fk_alters:
            try:
                await conn.execute(text(stmt))
            except Exception as exc:
                print(f"[db] FK alter skipped: {exc.__class__.__name__}")

    async with SessionLocal() as session:
        await seed_categories(session)
