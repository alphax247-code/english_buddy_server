"""
Database layer — backed by SQLAlchemy (PostgreSQL in production, SQLite locally).
Keeps the same public API as the old JSON-file version so app.py needs no changes.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from models import Base, User, Payment, Affiliate, Payout, PracticeSession, Promotion
from database import SessionLocal, engine

# ── XP / Level helpers (unchanged) ──────────────────────────────────────────

LEVELS = [
    {"name": "Beginner",             "min_xp": 0},
    {"name": "Elementary",           "min_xp": 100},
    {"name": "Pre-intermediate",     "min_xp": 300},
    {"name": "Intermediate",         "min_xp": 600},
    {"name": "Upper-intermediate",   "min_xp": 1000},
    {"name": "Advanced",             "min_xp": 1500},
]


def get_level(xp: int) -> dict:
    level = LEVELS[0]
    for lvl in LEVELS:
        if xp >= lvl["min_xp"]:
            level = lvl
    idx = LEVELS.index(level)
    next_level = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    return {
        "name": level["name"],
        "xp": xp,
        "next_level": next_level["name"] if next_level else None,
        "xp_to_next": (next_level["min_xp"] - xp) if next_level else 0,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Database class ────────────────────────────────────────────────────────────

class JSONDatabase:
    """SQLAlchemy-backed database. Tables are created/migrated by Alembic."""

    def __init__(self):
        # Safety net: create any missing tables (Alembic handles migrations).
        Base.metadata.create_all(engine, checkfirst=True)

    @contextmanager
    def _session(self):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # =====================================================
    # USER OPERATIONS
    # =====================================================

    def create_user(self, name: str, mobile: str, is_paid: bool = False,
                    role: str = "student", affiliate_code: Optional[str] = None) -> Dict[str, Any]:
        with self._session() as s:
            if s.query(User).filter_by(mobile=mobile).first():
                raise ValueError("User with this mobile already exists")
            user = User(
                name=name, mobile=mobile, is_paid=is_paid,
                role=role, affiliate_code=affiliate_code, created_at=_now()
            )
            s.add(user)
            s.flush()
            return user.to_dict()

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            u = s.get(User, user_id)
            return u.to_dict() if u else None

    def get_user_by_mobile(self, mobile: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            u = s.query(User).filter_by(mobile=mobile).first()
            return u.to_dict() if u else None

    def update_user(self, user_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            u = s.get(User, user_id)
            if not u:
                return None
            for k, v in kwargs.items():
                if hasattr(u, k):
                    setattr(u, k, v)
            s.flush()
            return u.to_dict()

    def delete_user(self, user_id: int) -> bool:
        with self._session() as s:
            u = s.get(User, user_id)
            if not u:
                return False
            s.delete(u)
            return True

    def get_all_users(self) -> List[Dict[str, Any]]:
        with self._session() as s:
            users = s.query(User).order_by(User.created_at.desc()).all()
            return [u.to_dict() for u in users]

    def get_users_by_affiliate(self, affiliate_code: str) -> List[Dict[str, Any]]:
        with self._session() as s:
            users = s.query(User).filter_by(affiliate_code=affiliate_code).all()
            return [u.to_dict() for u in users]

    # =====================================================
    # PAYMENT OPERATIONS
    # =====================================================

    def create_payment(self, name: str, mobile: str, reference: str, amount: int,
                       method: str = "mpesa", status: str = "pending",
                       provider_payment_id: Optional[str] = None,
                       checkout_url: Optional[str] = None,
                       affiliate_code: Optional[str] = None,
                       commission_amount: int = 0) -> Dict[str, Any]:
        with self._session() as s:
            if s.query(Payment).filter_by(reference=reference).first():
                raise ValueError("Payment with this reference already exists")
            p = Payment(
                name=name, mobile=mobile, reference=reference, amount=amount,
                method=method, status=status, provider_payment_id=provider_payment_id,
                checkout_url=checkout_url, affiliate_code=affiliate_code,
                commission_amount=commission_amount, created_at=_now()
            )
            s.add(p)
            s.flush()
            return p.to_dict()

    def get_payment_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            p = s.query(Payment).filter_by(reference=reference).first()
            return p.to_dict() if p else None

    def get_payments_by_mobile(self, mobile: str) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Payment).filter_by(mobile=mobile).order_by(Payment.created_at.desc()).all()
            return [p.to_dict() for p in rows]

    def update_payment(self, reference: str, **kwargs) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            p = s.query(Payment).filter_by(reference=reference).first()
            if not p:
                return None
            for k, v in kwargs.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            s.flush()
            return p.to_dict()

    def delete_payments_by_status(self, status: str) -> int:
        with self._session() as s:
            deleted = s.query(Payment).filter_by(status=status).delete()
            return deleted

    def get_all_payments(self) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Payment).order_by(Payment.created_at.desc()).all()
            return [p.to_dict() for p in rows]

    def get_payments_by_affiliate(self, affiliate_code: str,
                                  status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._session() as s:
            q = s.query(Payment).filter_by(affiliate_code=affiliate_code)
            if status:
                q = q.filter_by(status=status)
            return [p.to_dict() for p in q.all()]

    # =====================================================
    # AFFILIATE OPERATIONS
    # =====================================================

    def create_affiliate(self, code: str, name: str, mobile: Optional[str] = None,
                         commission_rate: int = 20, password: Optional[str] = None) -> Dict[str, Any]:
        with self._session() as s:
            if s.query(Affiliate).filter_by(code=code).first():
                raise ValueError("Affiliate with this code already exists")
            a = Affiliate(code=code, name=name, mobile=mobile,
                          commission_rate=commission_rate, password=password,
                          created_at=_now())
            s.add(a)
            s.flush()
            return a.to_dict()

    def get_affiliate_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            a = s.query(Affiliate).filter_by(code=code).first()
            return a.to_dict() if a else None

    def get_affiliate_by_id(self, affiliate_id: int) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            a = s.get(Affiliate, affiliate_id)
            return a.to_dict() if a else None

    def update_affiliate(self, affiliate_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            a = s.get(Affiliate, affiliate_id)
            if not a:
                return None
            for k, v in kwargs.items():
                if hasattr(a, k):
                    setattr(a, k, v)
            s.flush()
            return a.to_dict()

    def get_all_affiliates(self) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Affiliate).order_by(Affiliate.created_at.desc()).all()
            return [a.to_dict() for a in rows]

    # =====================================================
    # PAYOUT OPERATIONS
    # =====================================================

    def create_payout(self, affiliate_id: int, amount: int, method: str = "bank_transfer",
                      notes: Optional[str] = None, paid_by: Optional[str] = None,
                      provider_payout_id: Optional[str] = None,
                      provider_reference: Optional[str] = None,
                      status: str = "pending") -> Dict[str, Any]:
        with self._session() as s:
            a = s.get(Affiliate, affiliate_id)
            if not a:
                raise ValueError("Affiliate not found")
            now = _now()
            p = Payout(
                affiliate_id=affiliate_id, affiliate_code=a.code,
                affiliate_name=a.name, affiliate_mobile=a.mobile,
                amount=amount, method=method, status=status,
                provider_payout_id=provider_payout_id,
                provider_reference=provider_reference,
                notes=notes, paid_by=paid_by,
                created_at=now, updated_at=now
            )
            s.add(p)
            if status == "completed":
                a.total_earnings = max(0, a.total_earnings - amount)
            s.flush()
            return p.to_dict()

    def get_payout_by_id(self, payout_id: int) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            p = s.get(Payout, payout_id)
            return p.to_dict() if p else None

    def get_payouts_by_affiliate(self, affiliate_id: int) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Payout).filter_by(affiliate_id=affiliate_id).order_by(Payout.created_at.desc()).all()
            return [p.to_dict() for p in rows]

    def get_all_payouts(self) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Payout).order_by(Payout.created_at.desc()).all()
            return [p.to_dict() for p in rows]

    def update_payout(self, payout_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            p = s.get(Payout, payout_id)
            if not p:
                return None
            old_status = p.status
            for k, v in kwargs.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            p.updated_at = _now()
            if old_status == "pending" and p.status == "completed":
                a = s.get(Affiliate, p.affiliate_id)
                if a:
                    a.total_earnings = max(0, a.total_earnings - p.amount)
            s.flush()
            return p.to_dict()

    def add_affiliate_credit(self, affiliate_id: int, amount: int) -> Dict[str, Any]:
        with self._session() as s:
            a = s.get(Affiliate, affiliate_id)
            if not a:
                raise ValueError("Affiliate not found")
            a.total_earnings = (a.total_earnings or 0) + amount
            s.flush()
            return a.to_dict()

    # =====================================================
    # PROMOTIONS
    # =====================================================

    def create_promotion(self, name: str, description: str, extra_days: int,
                         start_date: str, end_date: str) -> Dict[str, Any]:
        with self._session() as s:
            promo = Promotion(
                name=name, description=description, extra_days=extra_days,
                start_date=start_date, end_date=end_date, created_at=_now()
            )
            s.add(promo)
            s.flush()
            return promo.to_dict()

    def get_all_promotions(self) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(Promotion).order_by(Promotion.created_at.desc()).all()
            return [p.to_dict() for p in rows]

    def get_active_promotions(self) -> List[Dict[str, Any]]:
        now = _now()
        with self._session() as s:
            rows = s.query(Promotion).filter(
                Promotion.is_active == True,
                Promotion.start_date <= now,
                Promotion.end_date >= now,
            ).all()
            return [p.to_dict() for p in rows]

    def update_promotion(self, promo_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self._session() as s:
            p = s.get(Promotion, promo_id)
            if not p:
                return None
            for k, v in kwargs.items():
                if hasattr(p, k):
                    setattr(p, k, v)
            s.flush()
            return p.to_dict()

    def delete_promotion(self, promo_id: int) -> bool:
        with self._session() as s:
            p = s.get(Promotion, promo_id)
            if not p:
                return False
            s.delete(p)
            return True

    # =====================================================
    # PRACTICE SESSION OPERATIONS
    # =====================================================

    def save_practice_session(self, user_id: int, conversation_id: int, scenario: str,
                               question: str, transcript: str, corrected: str,
                               grammar: str, pronunciation: str, examples: list,
                               grammar_score: int, pronunciation_score: int,
                               double_xp: bool = False) -> Dict[str, Any]:
        with self._session() as s:
            avg_score = round((grammar_score + pronunciation_score) / 2)
            base_xp = max(5, avg_score)
            xp_earned = base_xp * 2 if double_xp else base_xp

            session = PracticeSession(
                user_id=user_id, conversation_id=conversation_id,
                scenario=scenario, question=question, transcript=transcript,
                corrected=corrected, grammar=grammar, pronunciation=pronunciation,
                examples=examples, grammar_score=grammar_score,
                pronunciation_score=pronunciation_score, avg_score=avg_score,
                xp_earned=xp_earned, double_xp=double_xp, created_at=_now()
            )
            s.add(session)

            u = s.get(User, user_id)
            if u:
                u.xp = (u.xp or 0) + xp_earned
                u.total_sessions = (u.total_sessions or 0) + 1

            s.flush()
            return session.to_dict()

    def get_sessions_by_user(self, user_id: int) -> List[Dict[str, Any]]:
        with self._session() as s:
            rows = s.query(PracticeSession).filter_by(user_id=user_id).order_by(
                PracticeSession.created_at.desc()
            ).all()
            return [r.to_dict() for r in rows]

    def get_user_progress(self, user_id: int) -> Dict[str, Any]:
        with self._session() as s:
            u = s.get(User, user_id)
            if not u:
                return {}
            sessions = s.query(PracticeSession).filter_by(user_id=user_id).all()
            xp = u.xp or 0
            level_info = get_level(xp)
            avg_grammar = round(sum(r.grammar_score for r in sessions) / len(sessions), 1) if sessions else 0
            avg_pronunciation = round(sum(r.pronunciation_score for r in sessions) / len(sessions), 1) if sessions else 0
            recent = sorted(sessions, key=lambda x: x.created_at, reverse=True)[:10]
            return {
                "user_id": user_id,
                "name": u.name,
                "xp": xp,
                "level": level_info["name"],
                "next_level": level_info["next_level"],
                "xp_to_next": level_info["xp_to_next"],
                "total_sessions": u.total_sessions or 0,
                "avg_grammar_score": avg_grammar,
                "avg_pronunciation_score": avg_pronunciation,
                "recent_sessions": [r.to_dict() for r in recent],
            }

    # =====================================================
    # DATA MIGRATION (import from JSON snapshot)
    # =====================================================

    def load_snapshot(self, snapshot: Dict[str, Any]):
        """Import an old JSON-file snapshot into the relational tables (one-time migration)."""
        import json as _json

        def _coerce(v):
            # Convert non-JSON-serialisable types the old driver might have stored
            if isinstance(v, list):
                return _json.dumps(v)
            return v

        with self._session() as s:
            for u in snapshot.get("users", []):
                if not s.query(User).filter_by(mobile=u["mobile"]).first():
                    s.add(User(
                        id=u.get("id"), name=u["name"], mobile=u["mobile"],
                        role=u.get("role", "student"), is_paid=u.get("is_paid", False),
                        is_active=u.get("is_active", True), is_banned=u.get("is_banned", False),
                        affiliate_code=u.get("affiliate_code"),
                        xp=u.get("xp", 0), total_sessions=u.get("total_sessions", 0),
                        token_version=u.get("token_version", 0), device_id=u.get("device_id"),
                        created_at=u.get("created_at", _now())
                    ))

            for p in snapshot.get("payments", []):
                if not s.query(Payment).filter_by(reference=p["reference"]).first():
                    s.add(Payment(
                        id=p.get("id"), name=p["name"], mobile=p["mobile"],
                        reference=p["reference"], amount=p["amount"],
                        method=p.get("method", "mpesa"), status=p.get("status", "pending"),
                        provider_payment_id=p.get("provider_payment_id"),
                        provider_reference=p.get("provider_reference"),
                        checkout_url=p.get("checkout_url"),
                        affiliate_code=p.get("affiliate_code"),
                        commission_amount=p.get("commission_amount", 0),
                        commission_paid=p.get("commission_paid", False),
                        created_at=p.get("created_at", _now())
                    ))

            for a in snapshot.get("affiliates", []):
                if not s.query(Affiliate).filter_by(code=a["code"]).first():
                    s.add(Affiliate(
                        id=a.get("id"), code=a["code"], name=a["name"],
                        mobile=a.get("mobile"), commission_rate=a.get("commission_rate", 20),
                        total_referrals=a.get("total_referrals", 0),
                        total_earnings=a.get("total_earnings", 0),
                        is_active=a.get("is_active", True), password=a.get("password"),
                        password_reset_required=a.get("password_reset_required", False),
                        created_at=a.get("created_at", _now())
                    ))

            for p in snapshot.get("payouts", []):
                if not s.get(Payout, p.get("id")):
                    s.add(Payout(
                        id=p.get("id"), affiliate_id=p["affiliate_id"],
                        affiliate_code=p["affiliate_code"], affiliate_name=p["affiliate_name"],
                        affiliate_mobile=p.get("affiliate_mobile"),
                        amount=p["amount"], method=p.get("method", "bank_transfer"),
                        status=p.get("status", "pending"),
                        provider_payout_id=p.get("provider_payout_id"),
                        provider_reference=p.get("provider_reference"),
                        notes=p.get("notes"), paid_by=p.get("paid_by"),
                        created_at=p.get("created_at", _now()),
                        updated_at=p.get("updated_at", _now())
                    ))

            for ps in snapshot.get("practice_sessions", []):
                if not s.get(PracticeSession, ps.get("id")):
                    ex = ps.get("examples")
                    s.add(PracticeSession(
                        id=ps.get("id"), user_id=ps["user_id"],
                        conversation_id=ps.get("conversation_id", 0),
                        scenario=ps.get("scenario", ""), question=ps.get("question", ""),
                        transcript=ps.get("transcript"), corrected=ps.get("corrected"),
                        grammar=ps.get("grammar"), pronunciation=ps.get("pronunciation"),
                        examples=ex if isinstance(ex, list) else [],
                        grammar_score=ps.get("grammar_score", 0),
                        pronunciation_score=ps.get("pronunciation_score", 0),
                        avg_score=ps.get("avg_score", 0),
                        xp_earned=ps.get("xp_earned", 0),
                        double_xp=ps.get("double_xp", False),
                        created_at=ps.get("created_at", _now())
                    ))

        print("[DB] Snapshot import complete.")


# Global singleton
db = JSONDatabase()
