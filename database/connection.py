import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

DB_PATH = Path(os.environ.get("JOBZO_DB_PATH", str(Path.home() / ".jobzo" / "jobzo.db")))


def run_migrations(engine):
    """Run pending Alembic migrations. Idempotent."""
    try:
        from alembic.config import Config
        from alembic.command import upgrade
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
        cfg.set_main_option("sqlalchemy.url", str(engine.url))
        upgrade(cfg, "head")
    except Exception as e:
        import logging
        logging.getLogger("jobzo").warning("Migration skipped: %s", e)


def get_engine(db_path: str | None = None):
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False, connect_args={"timeout": 10})
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()
    run_migrations(engine)
    return engine


def get_session(engine=None) -> Session:
    if engine is None:
        engine = get_engine()
    return Session(engine)
