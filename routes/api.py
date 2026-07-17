"""API endpoints for payment flow and device authorization."""
import re
import logging
from functools import wraps
from flask import Blueprint, request, jsonify, current_app
from config import Config
from database import get_db
from models import Session, User, Payment, Plan
from services.payment import register_payment, confirm_payment
from services.scheduler import get_authorized_macs

logger = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
PHONE_RE = re.compile(r"^0[0-9]{9}$")  # Kenyan format: 07XXXXXXXX


def require_api_key(f):
    """Validate X-API-Key header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if key != Config.API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


def validate_mac(mac: str) -> bool:
    return bool(mac and MAC_RE.match(mac))


def validate_phone(phone: str) -> bool:
    return bool(phone and PHONE_RE.match(phone))


# ─── Health Check ───────────────────────────────────────────────────────────

@api_bp.route("/health", methods=["GET"])
@api_bp.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint for monitoring."""
    return jsonify({"status": "ok", "service": "wifi-hotspot-api"}), 200


# ─── Registration ───────────────────────────────────────────────────────────

@api_bp.route("/api/register", methods=["POST"])
@require_api_key
def register():
    """User selects a plan and enters phone number.

    Expects JSON: {mac_address, phone_number, plan_id}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    mac = data.get("mac_address", "").upper()
    phone = data.get("phone_number", "").strip()
    plan_id = data.get("plan_id")

    if not validate_mac(mac):
        return jsonify({"error": "Invalid MAC address format"}), 400
    if not validate_phone(phone):
        return jsonify({"error": "Invalid phone number (use 07XXXXXXXX)"}), 400
    if not plan_id:
        return jsonify({"error": "plan_id required"}), 400
    try:
        plan_id = int(plan_id)
    except (ValueError, TypeError):
        return jsonify({"error": "plan_id must be an integer"}), 400

    result = register_payment(mac, phone, plan_id)
    if result["success"]:
        return jsonify(result), 201
    return jsonify(result), 400


# ─── Payment Confirmation (from SMS monitor) ────────────────────────────────

@api_bp.route("/api/confirm", methods=["POST"])
@require_api_key
def confirm():
    """SMS monitor confirms payment.

    Expects JSON: {phone, amount, mpesa_code, raw_sms}
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    phone = data.get("phone", "").strip()
    amount = data.get("amount")
    mpesa_code = data.get("mpesa_code", "").strip()
    raw_sms = data.get("raw_sms", "")

    if not validate_phone(phone):
        return jsonify({"error": "Invalid phone number"}), 400
    if amount is None:
        return jsonify({"error": "amount required"}), 400
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400
    if not mpesa_code:
        return jsonify({"error": "mpesa_code required"}), 400

    result = confirm_payment(phone, amount, mpesa_code, raw_sms)
    if result["success"]:
        return jsonify(result), 200
    return jsonify(result), 400


# ─── My Status (unauthenticated — client checks own MAC) ────────────────────

@api_bp.route("/api/my-status", methods=["GET"])
def my_status():
    """Return session info for the requesting client's MAC.

    Query param: ?mac=XX:XX:XX:XX:XX:XX
    No authentication required — clients check their own status.
    """
    mac = request.args.get("mac", "").upper()
    if not validate_mac(mac):
        return jsonify({"error": "Valid MAC address required (?mac=XX:XX:XX:XX:XX:XX)"}), 400

    db = get_db()
    try:
        authorized_macs = get_authorized_macs()
        is_authorized = mac in [m.upper() for m in authorized_macs]

        session_obj = db.query(Session).filter(Session.mac_address == mac).first()
        user = db.query(User).filter(User.mac_address == mac).first()

        return jsonify({
            "mac_address": mac,
            "authorized": is_authorized,
            "session": session_obj.to_dict() if session_obj else None,
            "user": user.to_dict() if user else None,
        })
    finally:
        db.close()


# ─── Authorized MACs (for router controller) ────────────────────────────────

@api_bp.route("/api/authorized", methods=["GET"])
@require_api_key
def authorized():
    """Return list of currently authorized MAC addresses."""
    macs = get_authorized_macs()
    return jsonify({"macs": macs, "count": len(macs)})


@api_bp.route("/api/status/<mac>", methods=["GET"])
@require_api_key
def status(mac):
    """Check if a specific MAC is authorized."""
    mac = mac.upper()
    if not validate_mac(mac):
        return jsonify({"error": "Invalid MAC address format"}), 400

    authorized_macs = get_authorized_macs()
    is_authorized = mac in [m.upper() for m in authorized_macs]

    db = get_db()
    try:
        sess = db.query(Session).filter(Session.mac_address == mac).first()
        return jsonify({
            "mac_address": mac,
            "authorized": is_authorized,
            "session": sess.to_dict() if sess else None,
        })
    finally:
        db.close()


# ─── Revoke Access ──────────────────────────────────────────────────────────

@api_bp.route("/api/revoke", methods=["POST"])
def revoke():
    """Admin: revoke a user's access.

    Expects JSON: {mac_address}
    Protected by admin auth (checked inline).
    """
    auth = request.authorization
    if not auth or not _check_admin(auth.username, auth.password):
        return jsonify({"error": "Admin authentication required"}), 401

    data = request.get_json(silent=True)
    if not data or not data.get("mac_address"):
        return jsonify({"error": "mac_address required"}), 400

    mac = data["mac_address"].upper()
    if not validate_mac(mac):
        return jsonify({"error": "Invalid MAC address format"}), 400

    db = get_db()
    try:
        sess = db.query(Session).filter(Session.mac_address == mac).first()
        if sess:
            sess.is_active = False
            db.commit()
            logger.info(f"Revoked access for MAC={mac}")

            # Trigger whitelist sync
            _trigger_whitelist_sync()

            return jsonify({"success": True, "message": f"Access revoked for {mac}"})
        return jsonify({"error": "No active session found for this MAC"}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        db.close()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _check_admin(username: str, password: str) -> bool:
    """Validate admin credentials against settings table (hashed password)."""
    from werkzeug.security import check_password_hash
    db = get_db()
    try:
        from models import Setting
        stored_user = db.query(Setting).filter(Setting.key == "admin_username").first()
        stored_pass = db.query(Setting).filter(Setting.key == "admin_password").first()
        if not stored_user or not stored_pass:
            return False
        if stored_user.value != username:
            return False
        return check_password_hash(stored_pass.value, password)
    finally:
        db.close()


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
