"""Application configuration."""
import os
import secrets


class Config:
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', None) or secrets.token_hex(32)
    DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

    # PostgreSQL
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')
    DB_NAME = os.environ.get('DB_NAME', 'wifi_hotspot')
    DB_USER = os.environ.get('DB_USER', 'postgres')
    DB_PASS = os.environ.get('DB_PASS', 'postgres')
    SQLALCHEMY_DATABASE_URI = (
        f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Security
    API_KEY = os.environ.get('API_KEY', 'change-this-api-key')
    ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
    ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin123')

    # CORS — same-origin only (set ALLOWED_ORIGINS env var to override)
    CORS_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '').split(',') if os.environ.get('ALLOWED_ORIGINS') else []

    # Router (Nokia G-2425G-A)
    ROUTER_HOST = os.environ.get('ROUTER_HOST', '192.168.1.1')
    ROUTER_USER = os.environ.get('ROUTER_USER', 'admin')
    ROUTER_PASS = os.environ.get('ROUTER_PASS', 'admin')

    # Scheduler
    EXPIRE_INTERVAL = int(os.environ.get('EXPIRE_INTERVAL', '30'))  # seconds

    # Payment timeout (minutes)
    PAYMENT_TIMEOUT_MINUTES = int(os.environ.get('PAYMENT_TIMEOUT_MINUTES', '15'))
