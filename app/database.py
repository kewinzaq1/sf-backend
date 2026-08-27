from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(database_url: str) -> dict:
    if not database_url.startswith("sqlite"):
        return {}

    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in database_url or "mode=memory" in database_url:
        # A plain in-memory SQLite database lives and dies with its connection.
        # StaticPool keeps a single connection alive so every request — and every
        # thread FastAPI hands work to — sees the same data for the process's lifetime.
        kwargs["poolclass"] = StaticPool
    return kwargs


settings = get_settings()

engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    **_engine_kwargs(settings.database_url),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create tables. Called on startup; safe to call repeatedly."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    ensure_photo_column(engine)
    ensure_addresses_migrated(engine)


def ensure_photo_column(target_engine: Engine) -> None:
    """Add `contacts.photo` to a database created before the photo feature.

    `create_all()` only creates missing tables — it never alters existing
    ones — and persistent database URLs (file SQLite, Postgres) are a
    supported configuration. A single nullable column needs no migration
    framework: `ALTER TABLE ... ADD COLUMN` works on both dialects, and the
    inspection makes the call idempotent.
    """
    inspector = inspect(target_engine)
    if "contacts" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("contacts")}
    if "photo" in columns:
        return
    with target_engine.begin() as connection:
        connection.execute(text("ALTER TABLE contacts ADD COLUMN photo TEXT"))


def ensure_addresses_migrated(target_engine: Engine) -> None:
    """Copy legacy flat address columns into the `addresses` table.

    Contacts created before the one-to-many model stored one address as five
    columns on the contact row. Those columns are no longer mapped, so their
    data would silently disappear after upgrade. Each legacy row with any
    address data becomes one `home` address, skipping contacts that already
    have address rows — which makes the copy idempotent. The old columns are
    left in place: dropping columns is not portable across dialects, and the
    ORM simply never reads them again.
    """
    inspector = inspect(target_engine)
    tables = inspector.get_table_names()
    if "contacts" not in tables or "addresses" not in tables:
        return
    contact_columns = {column["name"] for column in inspector.get_columns("contacts")}
    legacy_columns = {"address", "city", "state", "postal_code", "country"}
    if not legacy_columns <= contact_columns:
        return
    with target_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO addresses (contact_id, type, street, city, state, postal_code, country)
                SELECT id, 'home', address, city, state, postal_code, country
                FROM contacts
                WHERE (address IS NOT NULL OR city IS NOT NULL OR state IS NOT NULL
                       OR postal_code IS NOT NULL OR country IS NOT NULL)
                  AND id NOT IN (SELECT contact_id FROM addresses)
                """
            )
        )


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
