"""Router control routes."""
import logging
from flask import Blueprint, request, jsonify
from config import Config
from functools import wraps
from services.scheduler import get_authorized_macs
from services.router_ctrl import apply_whitelist, get_current_whitelist

logger = logging.getLogger(__name__)
router_bp = Blueprint("router", __name__)


def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        if key != Config.API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@router_bp.route("/api/router/whitelist", methods=["POST"])
@require_api_key
def sync_whitelist():
    """Apply the current authorized MAC whitelist to the router.

    Called by the scheduler or manually for immediate sync.
    """
    macs = get_authorized_macs()
    result = apply_whitelist(macs)
    if result.get("success"):
        return jsonify(result), 200
    return jsonify(result), 500


@router_bp.route("/api/router/whitelist", methods=["GET"])
@require_api_key
def current_whitelist():
    """Return the current whitelist on the router."""
    macs = get_current_whitelist()
    return jsonify({"macs": macs, "count": len(macs)})
