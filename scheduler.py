"""Standalone scheduler entry point for systemd service.

Runs the auto-expire + router sync loop independently from Gunicorn.
Does NOT call create_app() to avoid starting the embedded scheduler thread.
"""
import logging
import os
import sys
import time

# Load .env if present
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from config import Config
from database import init_db
from services.scheduler import sync_router_whitelist


def main():
    logger.info("Starting standalone scheduler (interval=%ds)", Config.EXPIRE_INTERVAL)

    # Initialize database (creates tables + seeds)
    init_db()

    while True:
        try:
            sync_router_whitelist()
        except Exception as e:
            logger.exception("Scheduler tick failed: %s", e)
        time.sleep(Config.EXPIRE_INTERVAL)


if __name__ == "__main__":
    main()
