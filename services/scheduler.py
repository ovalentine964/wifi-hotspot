"""Scheduler: auto-expire sessions, payments, and sync router whitelist."""
import logging
from datetime import datetime, timedelta
from sqlalchemy import text
from database import get_db
from models import Session, User, Setting, Payment
from services.router_ctrl import apply_whitelist

logger = logging.getLogger(__name__)


def get_authorized_macs() -> list:
    """Return all currently authorized MAC addresses.

    Includes: VIP users, free users, paid users with active sessions.
    """
    db = get_db()
    try:
        now = datetime.utcnow()
        macs = set()

        # VIP and free users (permanent)
        perm_users = (
            db.query(User)
            .filter(User.plan_type.in_(["vip", "free"]), User.is_permanent == True)
            .all()
        )
        for u in perm_users:
            macs.add(u.mac_address.upper())

        # Active paid sessions
        active_sessions = (
            db.query(Session)
            .filter(Session.is_active == True, Session.expires_at > now)
            .all()
        )
        for s in active_sessions:
            macs.add(s.mac_address.upper())

        return list(macs)
    finally:
        db.close()


def expire_sessions():
    """Deactivate expired sessions."""
    db = get_db()
    try:
        now = datetime.utcnow()
        expired = (
            db.query(Session)
            .filter(Session.is_active == True, Session.expires_at <= now)
            .all()
        )
        count = 0
        for s in expired:
            s.is_active = False
            count += 1
            logger.info(f"Expired session: MAC={s.mac_address}")

        db.commit()
        if count:
            logger.info(f"Expired {count} session(s)")
        return count
    except Exception as e:
        db.rollback()
        logger.exception("Error expiring sessions")
        return 0
    finally:
        db.close()


def expire_pending_payments():
    """Expire pending payments older than the configured timeout (default 15 min).

    Called periodically by the scheduler.
    """
    from config import Config
    db = get_db()
    try:
        timeout = timedelta(minutes=Config.PAYMENT_TIMEOUT_MINUTES)
        cutoff = datetime.utcnow() - timeout
        expired = (
            db.query(Payment)
            .filter(Payment.status == "pending", Payment.created_at <= cutoff)
            .all()
        )
        count = 0
        for p in expired:
            p.status = "expired"
            count += 1
            logger.info(f"Expired pending payment: id={p.id}, phone={p.phone_number}")

        db.commit()
        if count:
            logger.info(f"Expired {count} pending payment(s)")
        return count
    except Exception as e:
        db.rollback()
        logger.exception("Error expiring pending payments")
        return 0
    finally:
        db.close()


def sync_router_whitelist():
    """Expire old sessions then push authorized MACs to the router."""
    expire_sessions()
    macs = get_authorized_macs()
    result = apply_whitelist(macs)
    if result.get("success"):
        logger.debug(f"Router sync: {result}")
    else:
        logger.warning(f"Router sync failed: {result}")
    return result


def run_scheduler(app, interval: int):
    """Run the scheduler loop inside a background thread."""
    import time
    import threading

    def _loop():
        with app.app_context():
            while True:
                try:
                    sync_router_whitelist()
                    expire_pending_payments()
                except Exception as e:
                    logger.exception("Scheduler tick failed")
                time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    logger.info(f"Scheduler started (interval={interval}s)")
