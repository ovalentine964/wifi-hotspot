"""Payment processing service."""
import logging
from datetime import datetime, timedelta
from sqlalchemy import text
from database import get_db
from models import Payment, Session, Plan

logger = logging.getLogger(__name__)


def register_payment(mac_address: str, phone_number: str, plan_id: int) -> dict:
    """Create a pending payment for a user."""
    db = get_db()
    try:
        plan = db.query(Plan).filter(Plan.id == plan_id, Plan.active == True).first()
        if not plan:
            return {"success": False, "error": "Invalid or inactive plan"}

        # Check for existing pending payment for this MAC+phone combo
        existing = (
            db.query(Payment)
            .filter(
                Payment.mac_address == mac_address,
                Payment.phone_number == phone_number,
                Payment.status == "pending",
            )
            .first()
        )
        if existing:
            return {
                "success": False,
                "error": "A pending payment already exists. Please wait or contact support.",
            }

        payment = Payment(
            mac_address=mac_address,
            phone_number=phone_number,
            amount=plan.price,
            plan_id=plan.id,
            status="pending",
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        logger.info(f"Pending payment created: MAC={mac_address}, plan={plan.name}, amount={plan.price}")
        return {
            "success": True,
            "payment": payment.to_dict(),
            "message": f"Pay KSh {plan.price} via M-Pesa. You will be connected automatically.",
        }
    except Exception as e:
        db.rollback()
        logger.exception("Error creating payment")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def confirm_payment(phone_number: str, amount: float, mpesa_code: str, raw_sms: str = None) -> dict:
    """Confirm a payment from SMS monitor.

    Matches a pending payment by phone number and amount.
    """
    db = get_db()
    try:
        # Find matching pending payment
        payment = (
            db.query(Payment)
            .filter(
                Payment.phone_number == phone_number,
                Payment.amount == amount,
                Payment.status == "pending",
            )
            .order_by(Payment.created_at.desc())
            .first()
        )
        if not payment:
            logger.warning(f"No pending payment for phone={phone_number}, amount={amount}")
            return {"success": False, "error": "No matching pending payment found"}

        plan = db.query(Plan).filter(Plan.id == payment.plan_id).first()
        if not plan:
            return {"success": False, "error": "Plan not found"}

        now = datetime.utcnow()
        expires_at = now + timedelta(hours=plan.duration_hours)

        # Confirm payment
        payment.status = "confirmed"
        payment.mpesa_code = mpesa_code
        payment.raw_sms = raw_sms
        payment.confirmed_at = now
        payment.expires_at = expires_at

        # Create or update session
        session = (
            db.query(Session).filter(Session.mac_address == payment.mac_address).first()
        )
        if session and session.is_active:
            # Extend existing session
            if session.expires_at and session.expires_at > now:
                session.expires_at = session.expires_at + timedelta(hours=plan.duration_hours)
            else:
                session.expires_at = expires_at
            session.phone_number = phone_number
            session.plan_id = plan.id
            session.is_active = True
        else:
            session = Session(
                mac_address=payment.mac_address,
                phone_number=phone_number,
                plan_id=plan.id,
                started_at=now,
                expires_at=expires_at,
                is_active=True,
            )
            db.add(session)

        db.commit()
        logger.info(
            f"Payment confirmed: phone={phone_number}, MAC={payment.mac_address}, "
            f"expires={expires_at}"
        )
        return {
            "success": True,
            "mac_address": payment.mac_address,
            "expires_at": expires_at.isoformat(),
            "plan": plan.name,
            "message": "Payment confirmed. You now have internet access.",
        }
    except Exception as e:
        db.rollback()
        logger.exception("Error confirming payment")
        return {"success": False, "error": str(e)}
    finally:
        db.close()
