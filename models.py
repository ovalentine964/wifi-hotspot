"""SQLAlchemy ORM models."""
from datetime import datetime, timedelta
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Numeric, Text, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    duration_hours = Column(Numeric(10,2), nullable=False)
    active = Column(Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "price": float(self.price),
            "duration_hours": self.duration_hours,
            "active": self.active,
        }


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(17), unique=True, nullable=False)
    phone_number = Column(String(15))
    plan_type = Column(String(10), default="paid")  # paid, vip, free
    is_permanent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "mac_address": self.mac_address,
            "phone_number": self.phone_number,
            "plan_type": self.plan_type,
            "is_permanent": self.is_permanent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(17), nullable=False)
    phone_number = Column(String(15), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"))
    status = Column(String(20), default="pending")  # pending, confirmed, expired, refunded
    mpesa_code = Column(String(20))
    raw_sms = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    confirmed_at = Column(DateTime)
    expires_at = Column(DateTime)

    plan = relationship("Plan")

    def to_dict(self):
        return {
            "id": self.id,
            "mac_address": self.mac_address,
            "phone_number": self.phone_number,
            "amount": float(self.amount),
            "plan_id": self.plan_id,
            "status": self.status,
            "mpesa_code": self.mpesa_code,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    mac_address = Column(String(17), unique=True, nullable=False)
    phone_number = Column(String(15))
    plan_id = Column(Integer, ForeignKey("plans.id"))
    started_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)

    plan = relationship("Plan")

    def to_dict(self):
        return {
            "id": self.id,
            "mac_address": self.mac_address,
            "phone_number": self.phone_number,
            "plan_id": self.plan_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "is_active": self.is_active,
        }


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)
