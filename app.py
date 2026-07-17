"""WiFi Hotspot Business API Server.

Main Flask application entry point.
"""
import logging
import secrets
from logging.handlers import RotatingFileHandler
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import Config


def _setup_logging(app: Flask):
    """Configure logging (stdout for cloud compatibility)."""
    import sys
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        stream=sys.stdout,
    )

    # Also try file handler (optional, works on persistent storage)
    try:
        file_handler = RotatingFileHandler(
            "wifi-hotspot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(log_format))
        app.logger.addHandler(file_handler)
        logging.getLogger().addHandler(file_handler)
    except (OSError, PermissionError):
        pass  # Read-only filesystem (e.g., Render free tier) — stdout only


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    # Logging with rotation
    _setup_logging(app)
    logger = logging.getLogger(__name__)

    # CORS — same-origin by default
    try:
        from flask_cors import CORS
        if Config.CORS_ORIGINS:
            CORS(app, origins=Config.CORS_ORIGINS)
        else:
            # Same-origin only — no cross-origin requests allowed
            CORS(app, origins=[], supports_credentials=True)
    except ImportError:
        logger.warning("flask-cors not installed; CORS not configured.")

    # Rate limiting
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["60 per minute"],
        storage_uri="memory://",
    )

    # CSRF protection
    try:
        from flask_wtf.csrf import CSRFProtect, generate_csrf
        csrf = CSRFProtect(app)

        @app.context_processor
        def inject_csrf():
            return dict(csrf_token=generate_csrf)
    except ImportError:
        logger.warning("flask-wtf not installed; CSRF protection disabled.")

    # Register blueprints
    from routes.portal import portal_bp
    from routes.api import api_bp
    from routes.admin import admin_bp
    from routes.router import router_bp

    app.register_blueprint(portal_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(router_bp)

    # Initialize database
    from database import init_db, close_db
    init_db()
    app.teardown_appcontext(close_db)

    # Schedule payment expiry job
    from services.scheduler import expire_pending_payments
    _schedule_payment_expiry(app)

    # Only start scheduler when running directly (not under Gunicorn)
    import sys
    if "gunicorn" not in sys.modules and not any("gunicorn" in arg for arg in sys.argv):
        from services.scheduler import run_scheduler
        run_scheduler(app, Config.EXPIRE_INTERVAL)

    logger.info("WiFi Hotspot API started.")
    return app


def _schedule_payment_expiry(app: Flask):
    """Schedule periodic pending-payment expiry checks."""
    import threading, time
    from services.scheduler import expire_pending_payments

    def _loop():
        with app.app_context():
            while True:
                try:
                    expire_pending_payments()
                except Exception as e:
                    logging.getLogger(__name__).exception("Payment expiry tick failed")
                time.sleep(60)  # check every 60 seconds

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)
