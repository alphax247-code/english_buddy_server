"""
Database layer backed by a private GitHub repository.

All data lives in a single database.json file in a private GitHub repo.
On startup the file is loaded into memory. Every write flushes back to GitHub,
so data survives Render deploys, restarts, and redeploys forever.

Required environment variables:
  GITHUB_DB_TOKEN  — Personal Access Token with repo read/write access
  GITHUB_DB_REPO   — "owner/repo-name"  (e.g. "alice/english-buddy-db")
  GITHUB_DB_FILE   — filename in the repo (default: "database.json")
  GITHUB_DB_BRANCH — branch to use (default: "main")
"""

import os
import json
import base64
import requests
from datetime import datetime, timezone
from threading import Lock
from typing import List, Optional, Dict, Any

# ── GitHub connection settings ───────────────────────────────────────────────

_GITHUB_TOKEN  = os.getenv("GITHUB_DB_TOKEN", "")
_GITHUB_REPO   = os.getenv("GITHUB_DB_REPO", "")
_GITHUB_FILE   = os.getenv("GITHUB_DB_FILE", "database.json")
_GITHUB_BRANCH = os.getenv("GITHUB_DB_BRANCH", "main")
_GITHUB_API    = "https://api.github.com"

# ── XP / Level helpers ───────────────────────────────────────────────────────

LEVELS = [
    {"name": "Beginner",           "min_xp": 0},
    {"name": "Elementary",         "min_xp": 100},
    {"name": "Pre-intermediate",   "min_xp": 300},
    {"name": "Intermediate",       "min_xp": 600},
    {"name": "Upper-intermediate", "min_xp": 1000},
    {"name": "Advanced",           "min_xp": 1500},
]

EMPTY_DB: Dict[str, Any] = {
    "users": [], "payments": [], "affiliates": [],
    "payouts": [], "practice_sessions": [], "promotions": [],
    "_counters": {
        "users": 0, "payments": 0, "affiliates": 0,
        "payouts": 0, "practice_sessions": 0, "promotions": 0,
    },
}


def get_level(xp: int) -> dict:
    level = LEVELS[0]
    for lvl in LEVELS:
        if xp >= lvl["min_xp"]:
            level = lvl
    idx = LEVELS.index(level)
    nxt = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    return {
        "name": level["name"], "xp": xp,
        "next_level": nxt["name"] if nxt else None,
        "xp_to_next": (nxt["min_xp"] - xp) if nxt else 0,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Low-level GitHub file I/O ────────────────────────────────────────────────

def _gh_headers() -> dict:
    return {
        "Authorization": f"token {_GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def _github_read() -> tuple[Dict[str, Any], str]:
    """Return (data_dict, file_sha). Raises on network/auth errors."""
    url = f"{_GITHUB_API}/repos/{_GITHUB_REPO}/contents/{_GITHUB_FILE}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": _GITHUB_BRANCH}, timeout=30)
    r.raise_for_status()
    info = r.json()
    sha = info["sha"]
    content = base64.b64decode(info["content"]).decode("utf-8")
    data = json.loads(content) if content.strip() else {}
    # Ensure all keys exist
    for key in EMPTY_DB:
        data.setdefault(key, EMPTY_DB[key] if not isinstance(EMPTY_DB[key], list) else list(EMPTY_DB[key]))
    return data, sha


def _github_write(data: Dict[str, Any], sha: str) -> str:
    """Commit data back to GitHub. Returns the new SHA."""
    url = f"{_GITHUB_API}/repos/{_GITHUB_REPO}/contents/{_GITHUB_FILE}"
    body_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    payload = {
        "message": "db: update",
        "content": base64.b64encode(body_bytes).decode("ascii"),
        "sha": sha,
        "branch": _GITHUB_BRANCH,
    }
    r = requests.put(url, headers=_gh_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["content"]["sha"]


# ── Database class ────────────────────────────────────────────────────────────

class JSONDatabase:
    """
    In-memory database backed by a private GitHub repo.
    All reads are served from the in-memory cache (fast).
    All writes flush to GitHub (persistent across deploys).
    """

    def __init__(self):
        self.lock = Lock()
        self._cache: Dict[str, Any] = {}
        self._sha: str = ""
        self._load()

    def _load(self):
        if not _GITHUB_TOKEN or not _GITHUB_REPO:
            print("[DB] GITHUB_DB_TOKEN / GITHUB_DB_REPO not set — using in-memory only (data lost on restart)")
            self._cache = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in EMPTY_DB.items()}
            return
        try:
            self._cache, self._sha = _github_read()
            print(f"[DB] Loaded from GitHub ({_GITHUB_REPO}/{_GITHUB_FILE})")
        except Exception as e:
            print(f"[DB] Could not load from GitHub: {e} — starting with empty DB")
            self._cache = {k: (list(v) if isinstance(v, list) else dict(v)) for k, v in EMPTY_DB.items()}

    def _read_data(self) -> Dict[str, Any]:
        return self._cache

    def _write_data(self, data: Dict[str, Any]):
        self._cache = data
        if not _GITHUB_TOKEN or not _GITHUB_REPO:
            return  # in-memory only mode
        try:
            self._sha = _github_write(data, self._sha)
        except Exception as e:
            print(f"[DB] GitHub write failed: {e}")

    def _get_next_id(self, table: str) -> int:
        counters = self._cache.setdefault("_counters", {})
        if table not in counters:
            existing = self._cache.get(table, [])
            counters[table] = max((r["id"] for r in existing), default=0)
        counters[table] += 1
        return counters[table]

    # ── Migration helper ──────────────────────────────────────────────────────

    def load_snapshot(self, snapshot: Dict[str, Any]):
        """Import a JSON snapshot (one-time migration from old database.json)."""
        with self.lock:
            data = self._read_data()
            for key in ["users", "payments", "affiliates", "payouts", "practice_sessions", "promotions"]:
                if snapshot.get(key) and not data.get(key):
                    data[key] = snapshot[key]
            if "_counters" not in data or not any(data["_counters"].values()):
                data["_counters"] = snapshot.get("_counters", EMPTY_DB["_counters"])
            self._write_data(data)
        print("[DB] Snapshot imported.")

    # =====================================================
    # USER OPERATIONS
    # =====================================================

    def create_user(self, name: str, mobile: str, is_paid: bool = False,
                    role: str = "student", affiliate_code: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            if any(u["mobile"] == mobile for u in data["users"]):
                raise ValueError("User with this mobile already exists")
            user = {
                "id": self._get_next_id("users"),
                "name": name, "mobile": mobile, "role": role,
                "is_paid": is_paid, "is_active": True, "is_banned": False,
                "affiliate_code": affiliate_code, "xp": 0, "total_sessions": 0,
                "token_version": 0, "device_id": None,
                "created_at": _now(),
            }
            data["users"].append(user)
            self._write_data(data)
            return user

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        return next((u for u in data["users"] if u["id"] == user_id), None)

    def get_user_by_mobile(self, mobile: str) -> Optional[Dict[str, Any]]:
        data = self._read_data()
        return next((u for u in data["users"] if u["mobile"] == mobile), None)

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
        return sorted(self._read_data()["users"], key=lambda x: x.get("created_at", ""), reverse=True)

    def get_users_by_affiliate(self, affiliate_code: str) -> List[Dict[str, Any]]:
        return [u for u in self._read_data()["users"] if u.get("affiliate_code") == affiliate_code]

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
            if any(p["reference"] == reference for p in data["payments"]):
                raise ValueError("Payment with this reference already exists")
            payment = {
                "id": self._get_next_id("payments"),
                "name": name, "mobile": mobile, "reference": reference,
                "amount": amount, "method": method, "status": status,
                "provider_payment_id": provider_payment_id,
                "provider_reference": None,
                "checkout_url": checkout_url,
                "affiliate_code": affiliate_code,
                "commission_amount": commission_amount,
                "commission_paid": False,
                "created_at": _now(),
            }
            data["payments"].append(payment)
            self._write_data(data)
            return payment

    def get_payment_by_reference(self, reference: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self._read_data()["payments"] if p["reference"] == reference), None)

    def get_payments_by_mobile(self, mobile: str) -> List[Dict[str, Any]]:
        payments = [p for p in self._read_data()["payments"] if p["mobile"] == mobile]
        return sorted(payments, key=lambda x: x.get("created_at", ""), reverse=True)

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
        return sorted(self._read_data()["payments"], key=lambda x: x.get("created_at", ""), reverse=True)

    def get_payments_by_affiliate(self, affiliate_code: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        payments = [p for p in self._read_data()["payments"] if p.get("affiliate_code") == affiliate_code]
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
            if any(a["code"] == code for a in data["affiliates"]):
                raise ValueError("Affiliate with this code already exists")
            affiliate = {
                "id": self._get_next_id("affiliates"),
                "code": code, "name": name, "mobile": mobile,
                "commission_rate": commission_rate, "total_referrals": 0,
                "total_earnings": 0, "is_active": True, "password": password,
                "password_reset_required": False, "created_at": _now(),
            }
            data["affiliates"].append(affiliate)
            self._write_data(data)
            return affiliate

    def get_affiliate_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        return next((a for a in self._read_data()["affiliates"] if a["code"] == code), None)

    def get_affiliate_by_id(self, affiliate_id: int) -> Optional[Dict[str, Any]]:
        return next((a for a in self._read_data()["affiliates"] if a["id"] == affiliate_id), None)

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
        return sorted(self._read_data()["affiliates"], key=lambda x: x.get("created_at", ""), reverse=True)

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
            affiliate = next((a for a in data["affiliates"] if a["id"] == affiliate_id), None)
            if not affiliate:
                raise ValueError("Affiliate not found")
            now = _now()
            payout = {
                "id": self._get_next_id("payouts"),
                "affiliate_id": affiliate_id,
                "affiliate_code": affiliate["code"],
                "affiliate_name": affiliate["name"],
                "affiliate_mobile": affiliate.get("mobile"),
                "amount": amount, "method": method, "status": status,
                "provider_payout_id": provider_payout_id,
                "provider_reference": provider_reference,
                "notes": notes, "paid_by": paid_by,
                "created_at": now, "updated_at": now,
            }
            data["payouts"].append(payout)
            if status == "completed":
                affiliate["total_earnings"] = max(0, affiliate.get("total_earnings", 0) - amount)
            self._write_data(data)
            return payout

    def get_payout_by_id(self, payout_id: int) -> Optional[Dict[str, Any]]:
        return next((p for p in self._read_data().get("payouts", []) if p["id"] == payout_id), None)

    def get_payouts_by_affiliate(self, affiliate_id: int) -> List[Dict[str, Any]]:
        payouts = [p for p in self._read_data().get("payouts", []) if p["affiliate_id"] == affiliate_id]
        return sorted(payouts, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_all_payouts(self) -> List[Dict[str, Any]]:
        return sorted(self._read_data().get("payouts", []), key=lambda x: x.get("created_at", ""), reverse=True)

    def update_payout(self, payout_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for payout in data.get("payouts", []):
                if payout["id"] == payout_id:
                    old_status = payout.get("status")
                    payout.update(kwargs)
                    payout["updated_at"] = _now()
                    if old_status == "pending" and payout.get("status") == "completed":
                        for a in data["affiliates"]:
                            if a["id"] == payout["affiliate_id"]:
                                a["total_earnings"] = max(0, a.get("total_earnings", 0) - payout["amount"])
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

    # =====================================================
    # PROMOTIONS
    # =====================================================

    def create_promotion(self, name: str, description: str, extra_days: int,
                         start_date: str, end_date: str) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            data.setdefault("promotions", [])
            promo = {
                "id": self._get_next_id("promotions"),
                "name": name, "description": description, "extra_days": extra_days,
                "start_date": start_date, "end_date": end_date,
                "is_active": True, "created_at": _now(),
            }
            data["promotions"].append(promo)
            self._write_data(data)
            return promo

    def get_all_promotions(self) -> List[Dict[str, Any]]:
        return sorted(self._read_data().get("promotions", []), key=lambda x: x.get("created_at", ""), reverse=True)

    def get_active_promotions(self) -> List[Dict[str, Any]]:
        now = _now()
        return [
            p for p in self._read_data().get("promotions", [])
            if p.get("is_active") and p.get("start_date", "") <= now <= p.get("end_date", "")
        ]

    def update_promotion(self, promo_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        with self.lock:
            data = self._read_data()
            for p in data.get("promotions", []):
                if p["id"] == promo_id:
                    p.update(kwargs)
                    self._write_data(data)
                    return p
            return None

    def delete_promotion(self, promo_id: int) -> bool:
        with self.lock:
            data = self._read_data()
            before = len(data.get("promotions", []))
            data["promotions"] = [p for p in data.get("promotions", []) if p["id"] != promo_id]
            if len(data["promotions"]) < before:
                self._write_data(data)
                return True
            return False

    # =====================================================
    # PRACTICE SESSION OPERATIONS
    # =====================================================

    def save_practice_session(self, user_id: int, conversation_id: int, scenario: str,
                               question: str, transcript: str, corrected: str,
                               grammar: str, pronunciation: str, examples: list,
                               grammar_score: int, pronunciation_score: int,
                               double_xp: bool = False) -> Dict[str, Any]:
        with self.lock:
            data = self._read_data()
            data.setdefault("practice_sessions", [])
            avg_score = round((grammar_score + pronunciation_score) / 2)
            base_xp = max(5, avg_score)
            xp_earned = base_xp * 2 if double_xp else base_xp
            session = {
                "id": self._get_next_id("practice_sessions"),
                "user_id": user_id, "conversation_id": conversation_id,
                "scenario": scenario, "question": question,
                "transcript": transcript, "corrected": corrected,
                "grammar": grammar, "pronunciation": pronunciation,
                "examples": examples, "grammar_score": grammar_score,
                "pronunciation_score": pronunciation_score,
                "avg_score": avg_score, "xp_earned": xp_earned,
                "double_xp": double_xp, "created_at": _now(),
            }
            data["practice_sessions"].append(session)
            for user in data["users"]:
                if user["id"] == user_id:
                    user["xp"] = user.get("xp", 0) + xp_earned
                    user["total_sessions"] = user.get("total_sessions", 0) + 1
                    break
            self._write_data(data)
            return session

    def get_sessions_by_user(self, user_id: int) -> List[Dict[str, Any]]:
        sessions = [s for s in self._read_data().get("practice_sessions", []) if s["user_id"] == user_id]
        return sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)

    def get_user_progress(self, user_id: int) -> Dict[str, Any]:
        data = self._read_data()
        user = next((u for u in data["users"] if u["id"] == user_id), None)
        if not user:
            return {}
        sessions = [s for s in data.get("practice_sessions", []) if s["user_id"] == user_id]
        xp = user.get("xp", 0)
        level_info = get_level(xp)
        avg_grammar = round(sum(s["grammar_score"] for s in sessions) / len(sessions), 1) if sessions else 0
        avg_pronunciation = round(sum(s["pronunciation_score"] for s in sessions) / len(sessions), 1) if sessions else 0
        return {
            "user_id": user_id, "name": user["name"], "xp": xp,
            "level": level_info["name"], "next_level": level_info["next_level"],
            "xp_to_next": level_info["xp_to_next"],
            "total_sessions": user.get("total_sessions", 0),
            "avg_grammar_score": avg_grammar,
            "avg_pronunciation_score": avg_pronunciation,
            "recent_sessions": sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)[:10],
        }


# Global singleton — imported by app.py
db = JSONDatabase()
