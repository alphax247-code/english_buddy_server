"""
JSON Database Manager
Simple file-based database using JSON for storage
"""
import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from threading import Lock


class JSONDatabase:
    """Thread-safe JSON file database"""

    def __init__(self, db_file: str = "database.json"):
        self.db_file = db_file
        self.lock = Lock()
        self._ensure_db_exists()

    def _ensure_db_exists(self):
        """Create database file with initial structure if it doesn't exist"""
        if not os.path.exists(self.db_file):
            initial_data = {
                "users": [],
                "payments": [],
                "affiliates": [],
                "payouts": [],
                "_counters": {
                    "users": 0,
                    "payments": 0,
                    "affiliates": 0,
                    "payouts": 0
                }
            }
            self._write_data(initial_data)

    def _read_data(self) -> Dict[str, Any]:
        """Read data from JSON file"""
        with open(self.db_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def _write_data(self, data: Dict[str, Any]):
        """Write data to JSON file"""
        with open(self.db_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _get_next_id(self, table: str, data: Dict[str, Any]) -> int:
        """Get next auto-increment ID for a table"""
        # Create counter if it doesn't exist
        if table not in data["_counters"]:
            data["_counters"][table] = 0
        data["_counters"][table] += 1
        return data["_counters"][table]

    # =====================================================
    # USER OPERATIONS
    # =====================================================

    def create_user(self, name: str, mobile: str, is_paid: bool = False,
                   role: str = "student", affiliate_code: Optional[str] = None) -> Dict[str, Any]:
        """Create a new user"""
        with self.lock:
            data = self._read_data()

            # Check if user already exists
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
                "affiliate_code": affiliate_code,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            data["users"].append(user)
            self._write_data(data)

            return user

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        data = self._read_data()
        for user in data["users"]:
            if user["id"] == user_id:
                return user
        return None

    def get_user_by_mobile(self, mobile: str) -> Optional[Dict[str, Any]]:
        """Get user by mobile number"""
        data = self._read_data()
        for user in data["users"]:
            if user["mobile"] == mobile:
                return user
        return None

    def update_user(self, user_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        """Update user fields"""
        with self.lock:
            data = self._read_data()

            for user in data["users"]:
                if user["id"] == user_id:
                    user.update(kwargs)
                    self._write_data(data)
                    return user

            return None

    def delete_user(self, user_id: int) -> bool:
        """Delete a user by ID"""
        with self.lock:
            data = self._read_data()
            before = len(data["users"])
            data["users"] = [u for u in data["users"] if u["id"] != user_id]
            if len(data["users"]) < before:
                self._write_data(data)
                return True
            return False

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all users"""
        data = self._read_data()
        return sorted(data["users"], key=lambda x: x["created_at"], reverse=True)

    def get_users_by_affiliate(self, affiliate_code: str) -> List[Dict[str, Any]]:
        """Get all users referred by an affiliate"""
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
        """Create a new payment"""
        with self.lock:
            data = self._read_data()

            # Check if payment with reference already exists
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
        """Get payment by reference"""
        data = self._read_data()
        for payment in data["payments"]:
            if payment["reference"] == reference:
                return payment
        return None

    def get_payments_by_mobile(self, mobile: str) -> List[Dict[str, Any]]:
        """Get all payments for a mobile number"""
        data = self._read_data()
        payments = [p for p in data["payments"] if p["mobile"] == mobile]
        return sorted(payments, key=lambda x: x["created_at"], reverse=True)

    def update_payment(self, reference: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Update payment fields"""
        with self.lock:
            data = self._read_data()

            for payment in data["payments"]:
                if payment["reference"] == reference:
                    payment.update(kwargs)
                    self._write_data(data)
                    return payment

            return None

    def get_all_payments(self) -> List[Dict[str, Any]]:
        """Get all payments"""
        data = self._read_data()
        return sorted(data["payments"], key=lambda x: x["created_at"], reverse=True)

    def get_payments_by_affiliate(self, affiliate_code: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all payments with an affiliate code"""
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
        """Create a new affiliate"""
        with self.lock:
            data = self._read_data()

            # Check if affiliate code already exists
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
                "password": password,  # Store hashed password
                "password_reset_required": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            data["affiliates"].append(affiliate)
            self._write_data(data)

            return affiliate

    def get_affiliate_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """Get affiliate by code"""
        data = self._read_data()
        for affiliate in data["affiliates"]:
            if affiliate["code"] == code:
                return affiliate
        return None

    def get_affiliate_by_id(self, affiliate_id: int) -> Optional[Dict[str, Any]]:
        """Get affiliate by ID"""
        data = self._read_data()
        for affiliate in data["affiliates"]:
            if affiliate["id"] == affiliate_id:
                return affiliate
        return None

    def update_affiliate(self, affiliate_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        """Update affiliate fields"""
        with self.lock:
            data = self._read_data()

            for affiliate in data["affiliates"]:
                if affiliate["id"] == affiliate_id:
                    affiliate.update(kwargs)
                    self._write_data(data)
                    return affiliate

            return None

    def get_all_affiliates(self) -> List[Dict[str, Any]]:
        """Get all affiliates"""
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
        """Create a new payout for an affiliate"""
        with self.lock:
            data = self._read_data()

            # Ensure payouts array exists
            if "payouts" not in data:
                data["payouts"] = []

            # Verify affiliate exists
            affiliate = None
            for a in data["affiliates"]:
                if a["id"] == affiliate_id:
                    affiliate = a
                    break

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

            # Only update affiliate's total_earnings if status is completed
            if status == "completed":
                for a in data["affiliates"]:
                    if a["id"] == affiliate_id:
                        a["total_earnings"] = max(0, a["total_earnings"] - amount)
                        break

            self._write_data(data)
            return payout

    def get_payout_by_id(self, payout_id: int) -> Optional[Dict[str, Any]]:
        """Get payout by ID"""
        data = self._read_data()
        for payout in data["payouts"]:
            if payout["id"] == payout_id:
                return payout
        return None

    def get_payouts_by_affiliate(self, affiliate_id: int) -> List[Dict[str, Any]]:
        """Get all payouts for an affiliate"""
        data = self._read_data()
        payouts = [p for p in data["payouts"] if p["affiliate_id"] == affiliate_id]
        return sorted(payouts, key=lambda x: x["created_at"], reverse=True)

    def get_all_payouts(self) -> List[Dict[str, Any]]:
        """Get all payouts"""
        data = self._read_data()
        if "payouts" not in data:
            data["payouts"] = []
            self._write_data(data)
        return sorted(data["payouts"], key=lambda x: x["created_at"], reverse=True)

    def update_payout(self, payout_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        """Update payout fields"""
        with self.lock:
            data = self._read_data()

            for payout in data.get("payouts", []):
                if payout["id"] == payout_id:
                    old_status = payout.get("status")
                    payout.update(kwargs)
                    payout["updated_at"] = datetime.now(timezone.utc).isoformat()

                    # If status changed from pending to completed, deduct from affiliate balance
                    if old_status == "pending" and payout.get("status") == "completed":
                        affiliate_id = payout["affiliate_id"]
                        amount = payout["amount"]
                        for a in data["affiliates"]:
                            if a["id"] == affiliate_id:
                                a["total_earnings"] = max(0, a["total_earnings"] - amount)
                                break

                    self._write_data(data)
                    return payout

            return None

    def add_affiliate_credit(self, affiliate_id: int, amount: int) -> Dict[str, Any]:
        """Add credit/earnings to an affiliate's balance"""
        with self.lock:
            data = self._read_data()

            # Find affiliate
            affiliate = None
            for a in data["affiliates"]:
                if a["id"] == affiliate_id:
                    affiliate = a
                    break

            if not affiliate:
                raise ValueError("Affiliate not found")

            # Add to earnings
            affiliate["total_earnings"] = affiliate.get("total_earnings", 0) + amount

            self._write_data(data)
            return affiliate


# Global database instance
db = JSONDatabase()
