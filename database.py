"""Database connection and migration utilities."""
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from config import Config

logger = logging.getLogger(__name__)

engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, pool_pre_ping=True)
SessionFactory = sessionmaker(bind=engine)
db_session = scoped_session(SessionFactory)


def get_db():
    """Get a database session."""
    return db_session()


def close_db(exception=None):
    """Close the database session."""
    db_session.remove()


def init_db():
    """Create tables and seed default data."""
    from models import Base
    Base.metadata.create_all(bind=engine)
    _seed_defaults()
    _seed_admin_settings()
    logger.info("Database initialized successfully.")


def _seed_defaults():
    """Insert default plans if table is empty."""
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM plans")).scalar()
        if count == 0:
            defaults = [
                ("1 Hour", 20.00, 1),
                ("3 Hours", 50.00, 3),
                ("1 Day", 100.00, 24),
                ("1 Week", 400.00, 168),
            ]
            for name, price, hours in defaults:
                conn.execute(
                    text("INSERT INTO plans (name, price, duration_hours) VALUES (:n, :p, :h)"),
                    {"n": name, "p": price, "h": hours},
                )
            conn.commit()
            logger.info("Seeded default plans.")


def _seed_admin_settings():
    """Insert default admin settings if missing. Passwords are hashed with werkzeug."""
    from werkzeug.security import generate_password_hash
    with engine.connect() as conn:
        # Username
        existing_user = conn.execute(
            text("SELECT 1 FROM settings WHERE key = :k"), {"k": "admin_username"}
        ).fetchone()
        if not existing_user:
            conn.execute(
                text("INSERT INTO settings (key, value) VALUES (:k, :v)"),
                {"k": "admin_username", "v": Config.ADMIN_USER},
            )

        # Password (hashed)
        existing_pass = conn.execute(
            text("SELECT 1 FROM settings WHERE key = :k"), {"k": "admin_password"}
        ).fetchone()
        if not existing_pass:
            hashed = generate_password_hash(Config.ADMIN_PASS)
            conn.execute(
                text("INSERT INTO settings (key, value) VALUES (:k, :v)"),
                {"k": "admin_password", "v": hashed},
            )

        conn.commit()
        logger.info("Admin settings seeded.")
