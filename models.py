"""
SQLAlchemy ORM models for English Buddy.
Each class maps to one database table.
"""
from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, JSON, Text
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    mobile = Column(String, unique=True, nullable=False)
    role = Column(String, default="student", nullable=False)
    is_paid = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_banned = Column(Boolean, default=False, nullable=False)
    affiliate_code = Column(String, nullable=True)
    xp = Column(Integer, default=0, nullable=False)
    total_sessions = Column(Integer, default=0, nullable=False)
    token_version = Column(Integer, default=0, nullable=False)
    device_id = Column(String, nullable=True)
    created_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mobile": self.mobile,
            "role": self.role,
            "is_paid": self.is_paid,
            "is_active": self.is_active,
            "is_banned": self.is_banned,
            "affiliate_code": self.affiliate_code,
            "xp": self.xp,
            "total_sessions": self.total_sessions,
            "token_version": self.token_version,
            "device_id": self.device_id,
            "created_at": self.created_at,
        }


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    mobile = Column(String, nullable=False)
    reference = Column(String, unique=True, nullable=False)
    amount = Column(Integer, nullable=False)
    method = Column(String, default="mpesa", nullable=False)
    status = Column(String, default="pending", nullable=False)
    provider_payment_id = Column(String, nullable=True)
    provider_reference = Column(String, nullable=True)
    checkout_url = Column(Text, nullable=True)
    affiliate_code = Column(String, nullable=True)
    commission_amount = Column(Integer, default=0, nullable=False)
    commission_paid = Column(Boolean, default=False, nullable=False)
    created_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mobile": self.mobile,
            "reference": self.reference,
            "amount": self.amount,
            "method": self.method,
            "status": self.status,
            "provider_payment_id": self.provider_payment_id,
            "provider_reference": self.provider_reference,
            "checkout_url": self.checkout_url,
            "affiliate_code": self.affiliate_code,
            "commission_amount": self.commission_amount,
            "commission_paid": self.commission_paid,
            "created_at": self.created_at,
        }


class Affiliate(Base):
    __tablename__ = "affiliates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    mobile = Column(String, nullable=True)
    commission_rate = Column(Integer, default=20, nullable=False)
    total_referrals = Column(Integer, default=0, nullable=False)
    total_earnings = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    password = Column(String, nullable=True)
    password_reset_required = Column(Boolean, default=False, nullable=False)
    created_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "mobile": self.mobile,
            "commission_rate": self.commission_rate,
            "total_referrals": self.total_referrals,
            "total_earnings": self.total_earnings,
            "is_active": self.is_active,
            "password": self.password,
            "password_reset_required": self.password_reset_required,
            "created_at": self.created_at,
        }


class Payout(Base):
    __tablename__ = "payouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    affiliate_id = Column(Integer, nullable=False)
    affiliate_code = Column(String, nullable=False)
    affiliate_name = Column(String, nullable=False)
    affiliate_mobile = Column(String, nullable=True)
    amount = Column(Integer, nullable=False)
    method = Column(String, default="bank_transfer", nullable=False)
    status = Column(String, default="pending", nullable=False)
    provider_payout_id = Column(String, nullable=True)
    provider_reference = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    paid_by = Column(String, nullable=True)
    created_at = Column(String, default=_now, nullable=False)
    updated_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "affiliate_id": self.affiliate_id,
            "affiliate_code": self.affiliate_code,
            "affiliate_name": self.affiliate_name,
            "affiliate_mobile": self.affiliate_mobile,
            "amount": self.amount,
            "method": self.method,
            "status": self.status,
            "provider_payout_id": self.provider_payout_id,
            "provider_reference": self.provider_reference,
            "notes": self.notes,
            "paid_by": self.paid_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PracticeSession(Base):
    __tablename__ = "practice_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    conversation_id = Column(Integer, nullable=False)
    scenario = Column(String, nullable=False)
    question = Column(Text, nullable=False)
    transcript = Column(Text, nullable=True)
    corrected = Column(Text, nullable=True)
    grammar = Column(Text, nullable=True)
    pronunciation = Column(Text, nullable=True)
    examples = Column(JSON, nullable=True)
    grammar_score = Column(Integer, default=0, nullable=False)
    pronunciation_score = Column(Integer, default=0, nullable=False)
    avg_score = Column(Integer, default=0, nullable=False)
    xp_earned = Column(Integer, default=0, nullable=False)
    double_xp = Column(Boolean, default=False, nullable=False)
    created_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "scenario": self.scenario,
            "question": self.question,
            "transcript": self.transcript,
            "corrected": self.corrected,
            "grammar": self.grammar,
            "pronunciation": self.pronunciation,
            "examples": self.examples,
            "grammar_score": self.grammar_score,
            "pronunciation_score": self.pronunciation_score,
            "avg_score": self.avg_score,
            "xp_earned": self.xp_earned,
            "double_xp": self.double_xp,
            "created_at": self.created_at,
        }


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    extra_days = Column(Integer, default=0, nullable=False)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(String, default=_now, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "extra_days": self.extra_days,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "is_active": self.is_active,
            "created_at": self.created_at,
        }
