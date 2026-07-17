"""Admin panel routes."""
import re
import logging
from functools import wraps
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.security import check_password_hash
from database import get_db
from models import User, Session, Payment, Plan, Setting

logger = logging.getLogger(__name__)
admin_bp = Blueprint("admin", __name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _validate_mac(mac: str) -> bool:
    return bool(mac and MAC_RE.match(mac))


def _get_admin_credentials(db):
    """Retrieve admin username and hashed password from settings."""
    u = db.query(Setting).filter(Setting.key == "admin_username").first()
    p = db.query(Setting).filter(Setting.key == "admin_password").first()
    return u, p


def admin_auth(f):
    """Check HTTP Basic auth against admin credentials in DB (password is hashed)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth:
            return jsonify({"error": "Authentication required"}), 401
        db = get_db()
        try:
            u, p = _get_admin_credentials(db)
            if not u or not p:
                return jsonify({"error": "Admin not configured"}), 500
            if u.value != auth.username:
                return jsonify({"error": "Invalid credentials"}), 401
            if not check_password_hash(p.value, auth.password):
                return jsonify({"error": "Invalid credentials"}), 401
        finally:
            db.close()
        return f(*args, **kwargs)
    return decorated


# ─── Admin Login Endpoint ───────────────────────────────────────────────────

@admin_bp.route("/api/admin/login", methods=["POST"])
def admin_login():
    """Authenticate admin and return success/token.

    Expects JSON: {username, password}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "username and password required"}), 400

    db = get_db()
    try:
        u, p = _get_admin_credentials(db)
        if not u or not p:
            return jsonify({"error": "Admin not configured"}), 500
        if u.value != username or not check_password_hash(p.value, password):
            return jsonify({"error": "Invalid credentials"}), 401

        # Set session flag for admin
        session["admin_authenticated"] = True
        session["admin_user"] = username
        return jsonify({"success": True, "message": "Authenticated"})
    finally:
        db.close()


# ─── Admin Dashboard ────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/stats", methods=["GET"])
@admin_auth
def stats():
    """Admin dashboard: revenue, active users, plan breakdown."""
    db = get_db()
    try:
        now = datetime.utcnow()

        # Revenue
        confirmed = db.query(Payment).filter(Payment.status == "confirmed").all()
        total_revenue = sum(float(p.amount) for p in confirmed)

        # Today's revenue
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_payments = [p for p in confirmed if p.confirmed_at and p.confirmed_at >= today_start]
        today_revenue = sum(float(p.amount) for p in today_payments)

        # Active sessions
        active_sessions = (
            db.query(Session)
            .filter(Session.is_active == True, Session.expires_at > now)
            .count()
        )

        # VIP / free users
        vip_count = db.query(User).filter(User.plan_type == "vip").count()
        free_count = db.query(User).filter(User.plan_type == "free").count()

        # Total registered users
        total_users = db.query(User).count()

        # Plan breakdown
        plans = db.query(Plan).filter(Plan.active == True).all()
        plan_stats = []
        for plan in plans:
            count = db.query(Payment).filter(
                Payment.plan_id == plan.id, Payment.status == "confirmed"
            ).count()
            plan_stats.append({"plan": plan.name, "sales": count})

        # Recent payments
        recent = (
            db.query(Payment)
            .order_by(Payment.created_at.desc())
            .limit(20)
            .all()
        )

        return jsonify({
            "total_revenue": total_revenue,
            "today_revenue": today_revenue,
            "active_sessions": active_sessions,
            "vip_users": vip_count,
            "free_users": free_count,
            "total_users": total_users,
            "plan_stats": plan_stats,
            "recent_payments": [p.to_dict() for p in recent],
        })
    finally:
        db.close()


# ─── VIP Management ─────────────────────────────────────────────────────────

@admin_bp.route("/api/admin/vip", methods=["POST"])
@admin_auth
def add_vip():
    """Add or upgrade a user to VIP.

    Expects JSON: {mac_address, phone_number?}
    """
    data = request.get_json(silent=True)
    if not data or not data.get("mac_address"):
        return jsonify({"error": "mac_address required"}), 400

    mac = data["mac_address"].upper()
    if not _validate_mac(mac):
        return jsonify({"error": "Invalid MAC address format (expected XX:XX:XX:XX:XX:XX)"}), 400

    phone = data.get("phone_number", "").strip() or None
    if phone and not re.match(r"^0[0-9]{9}$", phone):
        return jsonify({"error": "Invalid phone number format"}), 400

    db = get_db()
    try:
        user = db.query(User).filter(User.mac_address == mac).first()
        if user:
            user.plan_type = "vip"
            user.is_permanent = True
            if phone:
                user.phone_number = phone
        else:
            user = User(
                mac_address=mac,
                phone_number=phone,
                plan_type="vip",
                is_permanent=True,
            )
            db.add(user)

        # Also create a permanent session
        sess = db.query(Session).filter(Session.mac_address == mac).first()
        if sess:
            sess.is_active = True
            sess.expires_at = datetime(2099, 12, 31)
        else:
            sess = Session(
                mac_address=mac,
                phone_number=phone,
                expires_at=datetime(2099, 12, 31),
                is_active=True,
            )
            db.add(sess)

        db.commit()
        logger.info(f"VIP added: MAC={mac}")

        # Trigger immediate router whitelist sync
        _trigger_whitelist_sync()

        return jsonify({"success": True, "message": f"{mac} is now VIP"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


@admin_bp.route("/api/admin/vip", methods=["GET"])
@admin_auth
def list_vips():
    """List all VIP users."""
    db = get_db()
    try:
        vips = db.query(User).filter(User.plan_type == "vip").all()
        return jsonify({"vips": [u.to_dict() for u in vips], "count": len(vips)})
    finally:
        db.close()


@admin_bp.route("/api/admin/vip/remove", methods=["POST"])
@admin_auth
def remove_vip():
    """Remove VIP status from a user.

    Expects JSON: {mac_address}
    Immediately syncs removal to router.
    """
    data = request.get_json(silent=True)
    if not data or not data.get("mac_address"):
        return jsonify({"error": "mac_address required"}), 400

    mac = data["mac_address"].upper()
    if not _validate_mac(mac):
        return jsonify({"error": "Invalid MAC address format"}), 400

    db = get_db()
    try:
        user = db.query(User).filter(User.mac_address == mac, User.plan_type == "vip").first()
        if not user:
            return jsonify({"error": "VIP user not found"}), 404

        user.plan_type = "paid"
        user.is_permanent = False

        # Remove permanent session
        sess = db.query(Session).filter(Session.mac_address == mac).first()
        if sess:
            sess.is_active = False

        db.commit()
        logger.info(f"VIP removed: MAC={mac}")

        # Trigger immediate router whitelist sync (removes MAC from router)
        _trigger_whitelist_sync()

        return jsonify({"success": True, "message": f"VIP removed from {mac}"})
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Manual Payment Confirmation ────────────────────────────────────────────

@admin_bp.route("/api/admin/confirm", methods=["POST"])
@admin_auth
def manual_confirm():
    """Manually confirm a payment by phone+amount (fallback for SMS failures).

    Expects JSON: {phone_number, amount}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    phone = data.get("phone_number", "").strip()
    amount = data.get("amount")

    if not phone:
        return jsonify({"error": "phone_number required"}), 400
    if amount is None:
        return jsonify({"error": "amount required"}), 400

    from services.payment import confirm_payment
    result = confirm_payment(phone, float(amount), mpesa_code="MANUAL", raw_sms="Manual admin confirmation")
    if result["success"]:
        logger.info(f"Manual payment confirmed: phone={phone}, amount={amount}")
        return jsonify(result), 200
    return jsonify(result), 400


# ─── Admin Dashboard Page ───────────────────────────────────────────────────

@admin_bp.route("/admin")
def admin_page():
    """Serve admin dashboard HTML — requires session auth or triggers login."""
    # Check if already authenticated via session
    if session.get("admin_authenticated"):
        return render_template("admin.html")
    # Otherwise, the HTML page handles login via JS + Basic Auth
    return render_template("admin.html")


# ─── Helpers ────────────────────────────────────────────────────────────────

def _trigger_whitelist_sync():
    """Trigger an immediate router whitelist sync in background."""
    import threading
    def _sync():
        try:
            from services.scheduler import sync_router_whitelist
            sync_router_whitelist()
        except Exception as e:
            logger.exception("Background whitelist sync failed")
    threading.Thread(target=_sync, daemon=True).start()
