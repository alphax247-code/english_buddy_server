"""
Database Manager
Uses PostgreSQL when DATABASE_URL is set (production), falls back to JSON file (local dev).
"""
import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from threading import Lock

try:
    import psycopg2
    import psycopg2.extras
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_FILE = os.path.join(os.path.dirname(__file__), "database.json")

EMPTY_DB = {
    "users": [],
    "payments": [],
    "affiliates": [],
    "payouts": [],
    "_counters": {"users": 0, "payments": 0, "affiliates": 0, "payouts": 0}
}


class JSONDatabase:
    """Thread-safe database — PostgreSQL in production, JSON file locally."""

    def __init__(self):
        self.lock = Lock()
        self._use_postgres = bool(DATABASE_URL and HAS_PSYCOPG2)
        if self._use_postgres:
            self._init_postgres()
        else:
            self._ensure_file_exists()

    # =========================================================
    # POSTGRES BACKEND
    # =========================================================

    def _get_conn(self):
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url, sslmode="require")

    def _init_postgres(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS app_data (
                        id INTEGER PRIMARY KEY,
                        data JSONB NOT NULL
                    )
                """)
                cur.execute("""
                    INSERT INTO app_data (id, data)
                    VALUES (1, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (json.dumps(EMPTY_DB),))
            conn.commit()

    # =========================================================
    # JSON FILE BACKEND
    # =========================================================

    def _ensure_file_exists(self):
        if not os.path.exists(DATABASE_FILE):
            self._write_data(EMPTY_DB)

    # =========================================================
    # CORE READ / WRITE  (switches between backends)
    # =========================================================

    def _read_data(self) -> Dict[str, Any]:
        if self._use_postgres:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM app_data WHERE id = 1")
                    row = cur.fetchone()
                    data = row[0] if row else dict(EMPTY_DB)
            # Ensure _counters exists
            if "_counters" not in data:
                data["_counters"] = {"users": 0, "payments": 0, "affiliates": 0, "payouts": 0}
            return data
        else:
            with open(DATABASE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)

    def _write_data(self, data: Dict[str, Any]):
        if self._use_postgres:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE app_data SET data = %s WHERE id = 1",
                        (json.dumps(data, default=str),)
                    )
                conn.commit()
        else:
            with open(DATABASE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def _get_next_id(self, table: str, data: Dict[str, Any]) -> int:
        if "_counters" not in data:
            data["_counters"] = {}
        if table not in data["_counters"]:
            # Derive from existing records
            existing = data.get(table, [])
            data["_counters"][table] = max((r["id"] for r in existing), default=0)
        data["_counters"][table] += 1
        return data["_counters"][table]

    # =========================================================
    # MIGRATION — load a JSON snapshot into postgres
    # =========================================================

    def load_snapshot(self, snapshot: Dict[str, Any]):
        """Overwrite the database with a full JSON snapshot (used for migration)."""
        with self.lock:
            if "_counters" not in snapshot:
                snapshot["_counters"] = {
                    "users": max((u["id"] for u in snapshot.get("users", [])), default=0),
                    "payments": max((p["id"] for p in snapshot.get("payments", [])), default=0),
                    "affiliates": max((a["id"] for a in snapshot.get("affiliates", [])), default=0),
                    "payouts": max((p["id"] for p in snapshot.get("payouts", [])), default=0),
                }
            self._write_data(snapshot)

    # =====================================================
    # USER OPERATIONS
    # =====================================================

    def create_user(self, name: str, mobile: str, is_paid: bool = False,
                   role: str = "student", affiliate_code: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            for user in data["users"]:
                if user["mobile"] == mobile:
                    raise ValueError("User with this mobile already exists")
            user_id = self._get_next_id("users", data)
            user = {
                "id": user_id,
                "name": name,
                "mobile": mobile,
                "role": role,
                "is_paid": is_paid,
                "is_active": True,
                "is_banned": False,
                "affiliate_code": affiliate_code,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            data["users"].append(user)
            self._write_data(data)
            return user

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for user in data["users"]:
            if user["id"] == user_id:
                return user
        return None

    def get_user_by_mobile(self, mobile: str) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for user in data["users"]:
            if user["mobile"] == mobile:
                return user
        return None

    def update_user(self, user_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for user in data["users"]:
                if user["id"] == user_id:
                    user.update(kwargs)
                    self._write_data(data)
                    return user
            return None

    def delete_user(self, user_id: int) -> bool:
        with self.lock:
            data = self._read_data()
            before = len(data["users"])
            data["users"] = [u for u in data["users"] if u["id"] != user_id]
            if len(data["users"]) < before:
                self._write_data(data)
                return True
            return False

    def get_all_users(self) -> List[Dict[str, Any]]:
        data = self._read_data()
        return sorted(data["users"], key=lambda x: x["created_at"], reverse=True)

    def get_users_by_affiliate(self, affiliate_code: str) -> List[Dict[str, Any]]:
        data = self._read_data()
        return [u for u in data["users"] if u.get("affiliate_code") == affiliate_code]

    # =====================================================
    # PAYMENT OPERATIONS
    # =====================================================

    def create_payment(self, name: str, mobile: str, reference: str, amount: int,
                      method: str = "mpesa", status: str = "pending",
                      provider_payment_id: Optional[str] = None,
                      checkout_url: Optional[str] = None,
                      affiliate_code: Optional[str] = None,
                      commission_amount: int = 0) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            for payment in data["payments"]:
                if payment["reference"] == reference:
                    raise ValueError("Payment with this reference already exists")
            payment_id = self._get_next_id("payments", data)
            payment = {
                "id": payment_id,
                "name": name,
                "mobile": mobile,
                "reference": reference,
                "amount": amount,
                "method": method,
                "status": status,
                "provider_payment_id": provider_payment_id,
                "provider_reference": None,
                "checkout_url": checkout_url,
                "affiliate_code": affiliate_code,
                "commission_amount": commission_amount,
                "commission_paid": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            data["payments"].append(payment)
            self._write_data(data)
            return payment

    def get_payment_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for payment in data["payments"]:
            if payment["reference"] == reference:
                return payment
        return None

    def get_payments_by_mobile(self, mobile: str) -> List[Dict[str, Any]]:
        data = self._read_data()
        payments = [p for p in data["payments"] if p["mobile"] == mobile]
        return sorted(payments, key=lambda x: x["created_at"], reverse=True)

    def update_payment(self, reference: str, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for payment in data["payments"]:
                if payment["reference"] == reference:
                    payment.update(kwargs)
                    self._write_data(data)
                    return payment
            return None

    def delete_payments_by_status(self, status: str) -> int:
        with self.lock:
            data = self._read_data()
            before = len(data["payments"])
            data["payments"] = [p for p in data["payments"] if p["status"] != status]
            deleted = before - len(data["payments"])
            if deleted:
                self._write_data(data)
            return deleted

    def get_all_payments(self) -> List[Dict[str, Any]]:
        data = self._read_data()
        return sorted(data["payments"], key=lambda x: x["created_at"], reverse=True)

    def get_payments_by_affiliate(self, affiliate_code: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._read_data()
        payments = [p for p in data["payments"] if p.get("affiliate_code") == affiliate_code]
        if status:
            payments = [p for p in payments if p["status"] == status]
        return payments

    # =====================================================
    # AFFILIATE OPERATIONS
    # =====================================================

    def create_affiliate(self, code: str, name: str, mobile: Optional[str] = None,
                        commission_rate: int = 20, password: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            for affiliate in data["affiliates"]:
                if affiliate["code"] == code:
                    raise ValueError("Affiliate with this code already exists")
            affiliate_id = self._get_next_id("affiliates", data)
            affiliate = {
                "id": affiliate_id,
                "code": code,
                "name": name,
                "mobile": mobile,
                "commission_rate": commission_rate,
                "total_referrals": 0,
                "total_earnings": 0,
                "is_active": True,
                "password": password,
                "password_reset_required": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            data["affiliates"].append(affiliate)
            self._write_data(data)
            return affiliate

    def get_affiliate_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for affiliate in data["affiliates"]:
            if affiliate["code"] == code:
                return affiliate
        return None

    def get_affiliate_by_id(self, affiliate_id: int) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for affiliate in data["affiliates"]:
            if affiliate["id"] == affiliate_id:
                return affiliate
        return None

    def update_affiliate(self, affiliate_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for affiliate in data["affiliates"]:
                if affiliate["id"] == affiliate_id:
                    affiliate.update(kwargs)
                    self._write_data(data)
                    return affiliate
            return None

    def get_all_affiliates(self) -> List[Dict[str, Any]]:
        data = self._read_data()
        return sorted(data["affiliates"], key=lambda x: x["created_at"], reverse=True)

    # =====================================================
    # PAYOUT OPERATIONS
    # =====================================================

    def create_payout(self, affiliate_id: int, amount: int, method: str = "bank_transfer",
                     notes: Optional[str] = None, paid_by: Optional[str] = None,
                     provider_payout_id: Optional[str] = None,
                     provider_reference: Optional[str] = None,
                     status: str = "pending") -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            if "payouts" not in data:
                data["payouts"] = []
            affiliate = next((a for a in data["affiliates"] if a["id"] == affiliate_id), None)
            if not affiliate:
                raise ValueError("Affiliate not found")
            payout_id = self._get_next_id("payouts", data)
            payout = {
                "id": payout_id,
                "affiliate_id": affiliate_id,
                "affiliate_code": affiliate["code"],
                "affiliate_name": affiliate["name"],
                "affiliate_mobile": affiliate.get("mobile"),
                "amount": amount,
                "method": method,
                "status": status,
                "provider_payout_id": provider_payout_id,
                "provider_reference": provider_reference,
                "notes": notes,
                "paid_by": paid_by,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            data["payouts"].append(payout)
            if status == "completed":
                for a in data["affiliates"]:
                    if a["id"] == affiliate_id:
                        a["total_earnings"] = max(0, a["total_earnings"] - amount)
                        break
            self._write_data(data)
            return payout

    def get_payout_by_id(self, payout_id: int) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        for payout in data["payouts"]:
            if payout["id"] == payout_id:
                return payout
        return None

    def get_payouts_by_affiliate(self, affiliate_id: int) -> List[Dict[str, Any]]:
        data = self._read_data()
        payouts = [p for p in data["payouts"] if p["affiliate_id"] == affiliate_id]
        return sorted(payouts, key=lambda x: x["created_at"], reverse=True)

    def get_all_payouts(self) -> List[Dict[str, Any]]:
        data = self._read_data()
        if "payouts" not in data:
            data["payouts"] = []
            self._write_data(data)
        return sorted(data["payouts"], key=lambda x: x["created_at"], reverse=True)

    def update_payout(self, payout_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for payout in data.get("payouts", []):
                if payout["id"] == payout_id:
                    old_status = payout.get("status")
                    payout.update(kwargs)
                    payout["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if old_status == "pending" and payout.get("status") == "completed":
                        for a in data["affiliates"]:
                            if a["id"] == payout["affiliate_id"]:
                                a["total_earnings"] = max(0, a["total_earnings"] - payout["amount"])
                                break
                    self._write_data(data)
                    return payout
            return None

    def add_affiliate_credit(self, affiliate_id: int, amount: int) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            affiliate = next((a for a in data["affiliates"] if a["id"] == affiliate_id), None)
            if not affiliate:
                raise ValueError("Affiliate not found")
            affiliate["total_earnings"] = affiliate.get("total_earnings", 0) + amount
            self._write_data(data)
            return affiliate


# Global database instance
db = JSONDatabase()
