import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from config import settings
from models import Base

engine = create_async_engine(settings.db_url, echo=False, connect_args={"check_same_thread": False})
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def _migrate(conn):
    """
    Safe incremental migrations — each step is idempotent.
    New columns are added only if they don't exist yet.
    """

    # ── 1. users: add `role` column if missing ──────────────────
    cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()}

    if "role" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(10) NOT NULL DEFAULT 'user'"))
        # Carry over old is_admin flag → role = 'admin'
        await conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = 1"))
        print("[migrate] users.role added and backfilled from is_admin")

    if "email" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(120) NOT NULL DEFAULT ''"))
        print("[migrate] users.email added")

    if "security_question" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN security_question VARCHAR(200) NOT NULL DEFAULT ''"))
        print("[migrate] users.security_question added")

    if "security_answer_hash" not in cols:
        await conn.execute(text("ALTER TABLE users ADD COLUMN security_answer_hash VARCHAR NOT NULL DEFAULT ''"))
        print("[migrate] users.security_answer_hash added")

    # ── 2. event_templates: add theme column ──────────────────
    tables = {row[0] for row in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
    if "event_templates" in tables:
        et_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(event_templates)"))).fetchall()}
        if "theme" not in et_cols:
            await conn.execute(text("ALTER TABLE event_templates ADD COLUMN theme VARCHAR(20) NOT NULL DEFAULT 'default'"))
            print("[migrate] event_templates.theme added")

    # ── 3. events: add template + custom_values columns ─────────
    if "events" in tables:
        ev_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(events)"))).fetchall()}
        if "template_id" not in ev_cols:
            await conn.execute(text("ALTER TABLE events ADD COLUMN template_id VARCHAR REFERENCES event_templates(id) ON DELETE SET NULL"))
            print("[migrate] events.template_id added")
        if "custom_values" not in ev_cols:
            await conn.execute(text("ALTER TABLE events ADD COLUMN custom_values TEXT NOT NULL DEFAULT '{}'"))
            print("[migrate] events.custom_values added")

    # ── 4. participants: add joined_at column ──────────────────
    if "participants" in tables:
        p_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(participants)"))).fetchall()}
        if "joined_at" not in p_cols:
            # Note: We use CURRENT_TIMESTAMP for SQLite to fill existing rows
            await conn.execute(text("ALTER TABLE participants ADD COLUMN joined_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))
            print("[migrate] participants.joined_at added")

    # ── 3. Create new tables if they don't exist yet ─────────────
    #    (create_all handles this, but we call it after alters so
    #     FK references to the new columns are already in place)


async def init_db():
    os.makedirs(settings.data_dir, exist_ok=True)
    async with engine.begin() as conn:
        # Run migrations BEFORE create_all so existing tables are
        # already up-to-date when SQLAlchemy inspects them.
        await _migrate(conn)
        await conn.run_sync(Base.metadata.create_all)

