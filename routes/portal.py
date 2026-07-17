"""Portal page routes."""
from flask import Blueprint, render_template, request, jsonify
from config import Config
from database import get_db
from models import Plan

portal_bp = Blueprint("portal", __name__)


@portal_bp.route("/")
def index():
    """Serve the captive portal page."""
    db = get_db()
    try:
        plans = db.query(Plan).filter(Plan.active == True).all()
        return render_template(
            "portal.html",
            plans=[p.to_dict() for p in plans],
            api_key=Config.API_KEY,
        )
    finally:
        db.close()


@portal_bp.route("/api/plans")
def get_plans():
    """Return available plans."""
    db = get_db()
    try:
        plans = db.query(Plan).filter(Plan.active == True).all()
        return jsonify({"plans": [p.to_dict() for p in plans]})
    finally:
        db.close()
