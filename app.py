import os
import re
import hmac
import uuid
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import bcrypt
from dotenv import load_dotenv
from jose import jwt, JWTError
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from json_db import db
from payout_service import payout_service

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# =====================================================
# CONFIG
# =====================================================

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-secret")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-this-admin-password")
ALGORITHM = "HS256"

REGISTRATION_AMOUNT = 10

PAYSUITE_API_BASE = "https://paysuite.tech/api/v1"
PAYSUITE_API_TOKEN = os.getenv("PAYSUITE_API_TOKEN", "")
PAYSUITE_WEBHOOK_SECRET = os.getenv("PAYSUITE_WEBHOOK_SECRET", "")
RETURN_URL = os.getenv("RETURN_URL", "http://127.0.0.1:8000/payment-return")
CALLBACK_URL = os.getenv("CALLBACK_URL", "")

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")

# Support contact — set these in Render env vars (no app rebuild needed to change)
SUPPORT_WHATSAPP = os.getenv("SUPPORT_WHATSAPP", "")   # e.g. 258841234567
SUPPORT_PHONE    = os.getenv("SUPPORT_PHONE", "")       # e.g. +258841234567

async def _keep_alive():
    """Ping self every 14 minutes to prevent Render free tier spin-down."""
    if not RENDER_EXTERNAL_URL:
        return
    await asyncio.sleep(60)  # wait for server to be ready
    while True:
        try:
            requests.get(f"{RENDER_EXTERNAL_URL}/health", timeout=10)
        except Exception:
            pass
        await asyncio.sleep(14 * 60)

def _import_json_snapshot_if_needed():
    """One-time: if the new SQLAlchemy tables are empty, import database.json."""
    import json as _json
    local_file = os.path.join(os.path.dirname(__file__), "database.json")
    if not os.path.exists(local_file):
        return
    try:
        existing = db.get_all_users()
        if existing:
            print("[STARTUP] Tables already have data — skipping JSON import")
            return
        with open(local_file, "r", encoding="utf-8") as f:
            snapshot = _json.load(f)
        db.load_snapshot(snapshot)
        print("[STARTUP] Imported database.json into SQLAlchemy tables")
    except Exception as e:
        print(f"[STARTUP] JSON import check failed: {e}")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _import_json_snapshot_if_needed()
    asyncio.create_task(_poll_pending_payments())
    asyncio.create_task(_keep_alive())
    yield

app = FastAPI(lifespan=lifespan)

# Allow all origins — the app is a mobile client and the admin panel is served
# from the same Render domain, so a wildcard is safe here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
templates.env.cache = None  # Disable LRU cache (broken on Python 3.14)


# =====================================================
# REQUEST MODELS
# =====================================================

class LoginPayload(BaseModel):
    mobile: str
    password: Optional[str] = None
    device_id: Optional[str] = None

def _normalize_method(method: str) -> str:
    """Normalise payment method aliases to the canonical form PaYSuite expects."""
    m = method.strip().lower()
    if m in ("emola", "e-mola", "emola_mz"):
        return "e-mola"
    return m


class StartPaymentPayload(BaseModel):
    mobile: str
    name: str
    method: str = "mpesa"
    affiliate_code: Optional[str] = None

class CheckPaymentPayload(BaseModel):
    reference: str

class CheckPaymentByMobilePayload(BaseModel):
    mobile: str

class CreateAffiliatePayload(BaseModel):
    code: str
    name: str
    mobile: Optional[str] = None
    commission_rate: float = 20

class UpdateAffiliatePayload(BaseModel):
    name: Optional[str] = None
    mobile: Optional[str] = None
    commission_rate: Optional[float] = None
    is_active: Optional[bool] = None

class CreatePayoutPayload(BaseModel):
    affiliate_id: int
    amount: float
    method: str = "bank_transfer"
    notes: Optional[str] = None

class AddCreditPayload(BaseModel):
    amount: float
    reason: str = ""

class AdminLoginPayload(BaseModel):
    username: str
    password: str

class AffiliateLoginPayload(BaseModel):
    code: str
    password: str

class SetAffiliatePasswordPayload(BaseModel):
    password: str


# =====================================================
# HELPERS
# =====================================================

def process_mobile_number(mobile: str) -> Optional[str]:
    digits = re.sub(r"[^\d]", "", str(mobile))

    if len(digits) == 9:
        return "+258" + digits

    if len(digits) >= 10:
        return "+" + digits

    return None


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hashed password"""
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def create_token(user: dict) -> str:
    payload = {
        "user_id": user["id"],
        "role": user["role"],
        "token_version": user.get("token_version", 0),
        "exp": datetime.now(timezone.utc) + timedelta(days=7)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization[7:]
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = data.get("user_id")
        token_version = data.get("token_version", 0)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get_user_by_id(user_id)
    if not user:
        # Return 401 (not 404) so the Flutter app auto-logouts cleanly.
        # This happens if the account was deleted or the DB was reset.
        raise HTTPException(status_code=401, detail="Account not found. Please login again.")

    # Reject stale tokens — device switch bumps token_version in DB
    if token_version != user.get("token_version", 0):
        raise HTTPException(status_code=401, detail="Session expired. Please login again.")

    return user


def get_admin_user(user: dict = Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def verify_webhook_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        print("Missing X-Webhook-Signature header")
        return False

    if not PAYSUITE_WEBHOOK_SECRET:
        print("Missing PAYSUITE_WEBHOOK_SECRET")
        return False

    calculated = hmac.new(
        PAYSUITE_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    print("Calculated signature:", calculated)
    print("Received signature:  ", signature)

    return hmac.compare_digest(calculated, signature)


def update_affiliate_stats(payment: dict):
    if not payment.get("affiliate_code"):
        return

    affiliate = db.get_affiliate_by_code(payment["affiliate_code"])
    if affiliate:
        db.update_affiliate(
            affiliate["id"],
            total_referrals=affiliate["total_referrals"] + 1,
            total_earnings=affiliate["total_earnings"] + payment["commission_amount"]
        )
        db.update_payment(payment["reference"], commission_paid=True)
        print(f"Updated affiliate {affiliate['code']}: +1 referral, +{payment['commission_amount']} earnings")


# =====================================================
# PAGES
# =====================================================

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/contact")
def get_contact_info():
    """Public endpoint — app fetches admin contact details without needing auth."""
    return {
        "ok": True,
        "whatsapp": SUPPORT_WHATSAPP,
        "phone": SUPPORT_PHONE,
    }

@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/payment-return", response_class=HTMLResponse)
def payment_return_page(request: Request):
    return templates.TemplateResponse(request, "payment_return.html")


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html")


@app.get("/affiliate", response_class=HTMLResponse)
def affiliate_page(request: Request):
    return templates.TemplateResponse(request, "affiliate.html")


# =====================================================
# AUTH
# =====================================================

@app.post("/api/login")
def login(payload: LoginPayload):
    mobile = process_mobile_number(payload.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    # ── ADMIN: password provided and matches ──────────────────────────────
    if payload.password and payload.password == ADMIN_PASSWORD:
        admin_user = db.get_user_by_mobile("admin")
        if not admin_user:
            admin_user = db.create_user(name="Admin", mobile="admin", is_paid=True, role="admin")
        elif admin_user.get("role") != "admin":
            db.update_user(admin_user["id"], role="admin")
        new_version = admin_user.get("token_version", 0) + 1
        db.update_user(admin_user["id"], token_version=new_version)
        admin_user = db.get_user_by_id(admin_user["id"])
        token = create_token(admin_user)
        return {"ok": True, "token": token, "role": "admin", "redirect": "/admin",
                "user": {"id": admin_user["id"], "name": admin_user["name"], "role": "admin"}}

    # ── AFFILIATE: number matches an active affiliate's mobile ────────────
    for affiliate in db.get_all_affiliates():
        aff_mobile = process_mobile_number(affiliate.get("mobile", ""))
        if aff_mobile and aff_mobile == mobile and affiliate.get("is_active"):
            aff_user_mobile = f"affiliate_{affiliate['code']}"
            aff_user = db.get_user_by_mobile(aff_user_mobile)
            if not aff_user:
                aff_user = db.create_user(name=affiliate["name"], mobile=aff_user_mobile,
                                          is_paid=True, role="affiliate")
            # Bump token_version so old sessions are invalidated (same device-lock as regular users)
            new_version = aff_user.get("token_version", 0) + 1
            if payload.device_id:
                db.update_user(aff_user["id"], token_version=new_version, device_id=payload.device_id)
            else:
                db.update_user(aff_user["id"], token_version=new_version)
            aff_user = db.get_user_by_id(aff_user["id"])
            # Include affiliate_code and token_version in token
            payload_data = {
                "user_id": aff_user["id"],
                "affiliate_id": affiliate["id"],
                "affiliate_code": affiliate["code"],
                "role": "affiliate",
                "token_version": aff_user.get("token_version", 0),
                "exp": datetime.now(timezone.utc) + timedelta(days=30)
            }
            token = jwt.encode(payload_data, SECRET_KEY, algorithm=ALGORITHM)
            return {"ok": True, "token": token, "role": "affiliate", "redirect": "/affiliate",
                    "user": {"id": aff_user["id"], "name": affiliate["name"],
                             "mobile": mobile, "role": "affiliate",
                             "affiliate_code": affiliate["code"]}}

    # ── REGULAR USER ──────────────────────────────────────────────────────
    user = db.get_user_by_mobile(mobile)
    if not user:
        raise HTTPException(status_code=404, detail="Number not registered. Please register first.")
    if user.get("is_banned"):
        raise HTTPException(status_code=403, detail="Your account has been suspended.")
    if not user["is_paid"]:
        raise HTTPException(status_code=403, detail="Payment not completed.")

    # ── DEVICE LOCK ───────────────────────────────────────────────────────
    # Bump token_version on every login so any other active session is invalidated.
    # If a different device_id is presented, update stored device_id (new device takes over).
    new_version = user.get("token_version", 0) + 1
    if payload.device_id:
        db.update_user(user["id"], token_version=new_version, device_id=payload.device_id)
    else:
        db.update_user(user["id"], token_version=new_version)
    user = db.get_user_by_id(user["id"])  # reload with updated version

    token = create_token(user)
    return {"ok": True, "token": token, "role": user.get("role", "student"),
            "redirect": "/dashboard",
            "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}}


@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return {
        "ok": True,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "mobile": user["mobile"],
            "role": user["role"],
            "is_paid": user["is_paid"]
        }
    }


@app.post("/api/admin/login")
def admin_login(payload: AdminLoginPayload):
    if payload.username != "admin" or payload.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    admin_user = db.get_user_by_mobile("admin")
    if not admin_user:
        admin_user = db.create_user(name="Admin", mobile="admin", is_paid=True, role="admin")
    elif admin_user["role"] != "admin":
        db.update_user(admin_user["id"], role="admin")
        admin_user = db.get_user_by_id(admin_user["id"])

    token = create_token(admin_user)
    return {"ok": True, "token": token, "user": {"id": admin_user["id"], "name": admin_user["name"], "role": "admin"}}


@app.post("/api/affiliate/login")
def affiliate_login(payload: AffiliateLoginPayload):
    code = payload.code.strip().upper()
    password = payload.password

    affiliate = db.get_affiliate_by_code(code)
    if not affiliate:
        raise HTTPException(status_code=401, detail="Invalid affiliate code or password")

    if not affiliate.get("password"):
        raise HTTPException(status_code=401, detail="Password not set. Please contact admin.")

    if not verify_password(password, affiliate["password"]):
        raise HTTPException(status_code=401, detail="Invalid affiliate code or password")

    if not affiliate.get("is_active"):
        raise HTTPException(status_code=403, detail="Your affiliate account is inactive")

    # Create user record for affiliate if doesn't exist
    user = db.get_user_by_mobile(f"affiliate_{code}")
    if not user:
        user = db.create_user(
            name=affiliate["name"],
            mobile=f"affiliate_{code}",
            is_paid=True,
            role="affiliate"
        )
    elif user["role"] != "affiliate":
        db.update_user(user["id"], role="affiliate", name=affiliate["name"])
        user = db.get_user_by_id(user["id"])

    # Store affiliate_id in token for easy access
    payload_data = {
        "user_id": user["id"],
        "affiliate_id": affiliate["id"],
        "affiliate_code": code,
        "role": "affiliate",
        "exp": datetime.now(timezone.utc) + timedelta(days=30)
    }
    token = jwt.encode(payload_data, SECRET_KEY, algorithm=ALGORITHM)

    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "name": affiliate["name"],
            "role": "affiliate",
            "affiliate_code": code
        },
        "password_reset_required": affiliate.get("password_reset_required", False)
    }


@app.get("/api/my-payments")
def get_my_payments(user: dict = Depends(get_current_user)):
    payments = db.get_payments_by_mobile(user["mobile"])
    return {
        "ok": True,
        "payments": [
            {
                "id": p["id"],
                "name": p["name"],
                "reference": p["reference"],
                "amount": p["amount"],
                "method": p["method"],
                "status": p["status"],
                "checkout_url": p["checkout_url"],
                "created_at": p["created_at"]
            }
            for p in payments
        ]
    }


# =====================================================
# START PAYMENT
# =====================================================

@app.post("/api/register/start-payment")
def start_registration_payment(payload: StartPaymentPayload, authorization: str = Header(default="")):
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    mobile = process_mobile_number(payload.mobile)
    name = payload.name.strip()
    method = _normalize_method(payload.method)

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    if method not in {"mpesa", "e-mola", "credit_card"}:
        raise HTTPException(status_code=400, detail="Invalid payment method")

    # Resolve affiliate code: prefer explicit payload field, then JWT (affiliate scanning the QR)
    affiliate_code = None
    affiliate = None

    candidate_code = payload.affiliate_code or None
    if not candidate_code and authorization.startswith("Bearer "):
        try:
            token_data = jwt.decode(authorization[7:], SECRET_KEY, algorithms=[ALGORITHM])
            if token_data.get("role") == "affiliate" and token_data.get("affiliate_code"):
                candidate_code = token_data["affiliate_code"]
        except JWTError:
            pass

    if candidate_code:
        affiliate = db.get_affiliate_by_code(candidate_code)
        if affiliate and affiliate.get("is_active"):
            affiliate_code = candidate_code
        else:
            affiliate = None

    existing_user = db.get_user_by_mobile(mobile)
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    reference = f"REG{int(datetime.now(timezone.utc).timestamp())}{mobile[-4:]}{uuid.uuid4().hex[:6].upper()}"

    # Strip the leading '+' so PaYSuite gets a plain E.164 string without plus sign
    mobile_digits = mobile.lstrip("+")

    # Determine the best return_url:
    # - prefer the explicit RETURN_URL env var
    # - fall back to the deep link so the app re-opens automatically after checkout
    # - never use localhost (PaYSuite rejects it)
    effective_return_url = RETURN_URL
    if not effective_return_url or "127.0.0.1" in effective_return_url or "localhost" in effective_return_url:
        effective_return_url = f"englishbuddy://payment/registration"

    body = {
        "amount": REGISTRATION_AMOUNT,       # PaYSuite expects a number, not a string
        "method": method,
        "reference": reference,
        "description": f"English Buddy registration for {name}",
        "return_url": f"{effective_return_url}?reference={reference}",
        # PaYSuite requires the payer phone for USSD-push methods (eMola, M-Pesa).
        "phone": mobile_digits,
        "phone_number": mobile_digits,
        "msisdn": mobile_digits,             # older PaYSuite versions use this key
    }

    if CALLBACK_URL:
        body["callback_url"] = CALLBACK_URL

    headers = {
        "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    print("Creating Paysuite payment...")
    print("Payload:", body)

    try:
        response = requests.post(
            f"{PAYSUITE_API_BASE}/payments",
            json=body,
            headers=headers,
            timeout=30
        )
        print("Paysuite status:", response.status_code)
        print("Paysuite response:", response.text)
        provider = response.json()
    except requests.exceptions.Timeout:
        print("Paysuite request timed out")
        raise HTTPException(status_code=502, detail="Não foi possível contactar o servidor de pagamento. Tente novamente.")
    except requests.RequestException as e:
        print("Paysuite request failed:", str(e))
        raise HTTPException(status_code=502, detail="Erro de ligação ao servidor de pagamento. Tente novamente.")
    except ValueError:
        print("Paysuite returned invalid JSON")
        raise HTTPException(status_code=502, detail="Paysuite returned invalid JSON")

    if response.status_code not in (200, 201):
        ps_message = provider.get("message") or provider.get("error") or "Paysuite payment creation failed"
        print(f"Paysuite error HTTP {response.status_code}: {response.text}")
        raise HTTPException(
            status_code=502,
            detail=f"[HTTP {response.status_code}] {ps_message}"
        )

    data = provider.get("data", {})
    checkout_url = data.get("checkout_url")
    provider_payment_id = data.get("id")

    transaction = data.get("transaction", {})
    status = transaction.get("status", "pending") if transaction else "pending"
    internal_status = "success" if status in ("completed", "paid") else status

    commission_amount = 0
    if affiliate:
        commission_amount = int(REGISTRATION_AMOUNT * affiliate["commission_rate"] / 100)

    payment = db.create_payment(
        name=name,
        mobile=mobile,
        reference=reference,
        amount=REGISTRATION_AMOUNT,
        method=method,
        status=internal_status,
        provider_payment_id=provider_payment_id,
        checkout_url=checkout_url,
        affiliate_code=affiliate_code,
        commission_amount=commission_amount
    )

    return {
        "ok": True,
        "message": "Payment started",
        "payment": {
            "reference": payment["reference"],
            "amount": payment["amount"],
            "method": payment["method"],
            "status": payment["status"],
            "checkout_url": payment["checkout_url"],
            # True when the method is USSD push (no browser redirect needed)
            "is_ussd_push": payment["method"] in ("e-mola", "mpesa"),
        }
    }


# =====================================================
# CHECK PAYMENT STATUS
# =====================================================

@app.post("/api/register/check-payment-status")
def check_payment_status(payload: CheckPaymentPayload):
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    reference = payload.reference.strip()
    if not reference:
        raise HTTPException(status_code=400, detail="Reference is required")

    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment["status"] == "success":
        user = db.get_user_by_mobile(payment["mobile"])
        if user:
            token = create_token(user)
            return {
                "ok": True,
                "status": "success",
                "message": "Payment already completed",
                "token": token,
                "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}
            }

    if not payment.get("provider_payment_id"):
        return {
            "ok": True,
            "status": payment["status"],
            "message": "Payment is pending — waiting for provider confirmation.",
        }

    headers = {
        "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
        "Accept": "application/json"
    }

    print(f"Checking payment status for: {reference}")

    try:
        response = requests.get(
            f"{PAYSUITE_API_BASE}/payments/{payment['provider_payment_id']}",
            headers=headers,
            timeout=30
        )
        print("Paysuite status check:", response.status_code)
        print("Paysuite response:", response.text)
        provider = response.json()
    except requests.RequestException as e:
        print("Paysuite status check failed:", str(e))
        return {
            "ok": False,
            "status": payment["status"],
            "message": "Unable to check payment status from provider. Please try again later or contact support.",
            "error": "network_error",
            "payment": {
                "reference": payment["reference"],
                "amount": payment["amount"],
                "current_status": payment["status"]
            }
        }
    except ValueError:
        return {
            "ok": False,
            "status": payment["status"],
            "message": "Invalid response from payment provider",
            "error": "invalid_response"
        }

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=provider.get("message", "Failed to check payment status"))

    data = provider.get("data", {})
    transaction = data.get("transaction") or {}
    paysuite_status = transaction.get("status", "pending")

    print(f"Payment status from Paysuite: {paysuite_status}")
    print(f"Transaction data: {transaction}")

    if paysuite_status in ("completed", "paid"):
        internal_status = "success"
    elif paysuite_status in ("failed", "cancelled"):
        internal_status = paysuite_status
    else:
        internal_status = "pending"

    db.update_payment(reference, status=internal_status)
    payment = db.get_payment_by_reference(reference)

    if internal_status == "success":
        existing_user = db.get_user_by_mobile(payment["mobile"])

        if not existing_user:
            user = db.create_user(
                name=payment["name"],
                mobile=payment["mobile"],
                is_paid=True,
                affiliate_code=payment.get("affiliate_code")
            )
            print("User created after payment confirmation:", payment["mobile"])
            update_affiliate_stats(payment)
        else:
            db.update_user(
                existing_user["id"],
                is_paid=True,
                is_active=True,
                affiliate_code=existing_user.get("affiliate_code") or payment.get("affiliate_code")
            )
            user = db.get_user_by_id(existing_user["id"])
            print("Existing user marked as paid:", payment["mobile"])
            if not payment.get("commission_paid"):
                update_affiliate_stats(payment)

        token = create_token(user)
        return {
            "ok": True,
            "status": "success",
            "message": "Payment confirmed",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}
        }

    return {"ok": True, "status": internal_status, "message": f"Payment status: {internal_status}"}


@app.post("/api/register/check-payment-by-mobile")
def check_payment_by_mobile(payload: CheckPaymentByMobilePayload):
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    mobile = process_mobile_number(payload.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    user = db.get_user_by_mobile(mobile)
    if user and user["is_paid"]:
        token = create_token(user)
        return {
            "ok": True,
            "status": "success",
            "message": "User already registered",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}
        }

    payments = db.get_payments_by_mobile(mobile)
    if not payments:
        raise HTTPException(status_code=404, detail="No payment found for this mobile number")

    payment = payments[0]

    if payment["status"] == "success":
        if not user:
            user = db.create_user(
                name=payment["name"],
                mobile=payment["mobile"],
                is_paid=True
            )
        token = create_token(user)
        return {
            "ok": True,
            "status": "success",
            "message": "Payment already completed",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}
        }

    if not payment.get("provider_payment_id"):
        return {
            "ok": True,
            "status": payment["status"],
            "message": "Payment is pending — waiting for provider confirmation.",
            "payment": {
                "reference": payment["reference"],
                "amount": payment["amount"],
                "checkout_url": payment.get("checkout_url"),
            }
        }

    headers = {
        "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
        "Accept": "application/json"
    }

    print(f"Checking payment status for mobile: {mobile} (payment_id: {payment['provider_payment_id']})")

    try:
        response = requests.get(
            f"{PAYSUITE_API_BASE}/payments/{payment['provider_payment_id']}",
            headers=headers,
            timeout=30
        )
        print("Paysuite status check:", response.status_code)
        print("Paysuite response:", response.text)
        result = response.json()
    except requests.RequestException as e:
        print("Paysuite status check failed:", str(e))
        return {
            "ok": False,
            "status": payment["status"],
            "message": "Unable to check payment status from provider. Please try again later or contact support.",
            "error": "network_error",
            "payment": {
                "reference": payment["reference"],
                "amount": payment["amount"],
                "current_status": payment["status"],
                "checkout_url": payment.get("checkout_url")
            }
        }
    except ValueError:
        return {
            "ok": False,
            "status": payment["status"],
            "message": "Invalid response from payment provider",
            "error": "invalid_response"
        }

    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=result.get("message", "Failed to check payment status"))

    data = result.get("data", {})
    transaction = data.get("transaction") or {}
    paysuite_status = transaction.get("status", "pending")

    print(f"Payment status from Paysuite: {paysuite_status}")
    print(f"Transaction data: {transaction}")

    if paysuite_status in ("completed", "paid"):
        internal_status = "success"
    elif paysuite_status in ("failed", "cancelled"):
        internal_status = paysuite_status
    else:
        internal_status = "pending"

    db.update_payment(payment["reference"], status=internal_status)
    payment = db.get_payment_by_reference(payment["reference"])

    if internal_status == "success":
        if not user:
            user = db.create_user(
                name=payment["name"],
                mobile=payment["mobile"],
                is_paid=True,
                affiliate_code=payment.get("affiliate_code")
            )
            print("User created after payment confirmation:", payment["mobile"])
            update_affiliate_stats(payment)
        else:
            db.update_user(
                user["id"],
                is_paid=True,
                is_active=True,
                affiliate_code=user.get("affiliate_code") or payment.get("affiliate_code")
            )
            user = db.get_user_by_id(user["id"])
            print("Existing user marked as paid:", payment["mobile"])
            if not payment.get("commission_paid"):
                update_affiliate_stats(payment)

        token = create_token(user)
        return {
            "ok": True,
            "status": "success",
            "message": "Payment confirmed",
            "token": token,
            "user": {"id": user["id"], "name": user["name"], "mobile": user["mobile"]}
        }

    return {"ok": True, "status": internal_status, "message": f"Payment status: {internal_status}"}


# =====================================================
# WEBHOOK
# =====================================================

@app.post("/api/paysuite/webhook")
async def paysuite_webhook(request: Request):
    try:
        raw_body = await request.body()
        signature = request.headers.get("X-Webhook-Signature")

        print("\n========== WEBHOOK RECEIVED ==========")
        print("Raw body:", raw_body.decode("utf-8", errors="ignore"))
        print("Signature header:", signature)

        if not verify_webhook_signature(raw_body, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        payload = await request.json()
        print("Webhook JSON:", payload)

        event = payload.get("event")
        data = payload.get("data", {})
        request_id = payload.get("request_id")

        reference = data.get("reference")
        provider_payment_id = data.get("id")

        print("Event:", event)
        print("Reference:", reference)
        print("Provider payment ID:", provider_payment_id)

        if not reference:
            raise HTTPException(status_code=400, detail="Missing payment reference")

        payment = db.get_payment_by_reference(reference)
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        db.update_payment(
            reference,
            provider_reference=request_id,
            provider_payment_id=provider_payment_id
        )

        if event == "payment.success":
            db.update_payment(reference, status="success")
            payment = db.get_payment_by_reference(reference)
            existing_user = db.get_user_by_mobile(payment["mobile"])

            if not existing_user:
                db.create_user(
                    name=payment["name"],
                    mobile=payment["mobile"],
                    is_paid=True
                )
                print("User created after successful payment:", payment["mobile"])
            else:
                db.update_user(existing_user["id"], is_paid=True, is_active=True)
                print("Existing user updated as paid:", payment["mobile"])

        elif event == "payment.failed":
            db.update_payment(reference, status="failed")
            print("Payment marked as failed")

        elif event == "payment.cancelled":
            db.update_payment(reference, status="cancelled")
            print("Payment marked as cancelled")

        else:
            print("Unhandled event type:", event)

        print("Webhook processed successfully")
        print("======================================\n")

        return {"ok": True, "message": "Webhook processed"}

    except HTTPException:
        raise
    except Exception as e:
        print("WEBHOOK ERROR:", str(e))
        raise HTTPException(status_code=500, detail=f"Webhook processing failed: {str(e)}")


# =====================================================
# ADMIN - IMPORT PAYMENTS FROM PAYSUITE
# =====================================================

def _resolve_paysuite_status(paysuite_status: str) -> str:
    if paysuite_status in ("completed", "paid"):
        return "success"
    if paysuite_status in ("failed", "cancelled"):
        return paysuite_status
    return "pending"


def _process_imported_payment(p: dict):
    """Create or update a local payment record from a Paysuite payment object."""
    reference = p.get("reference", "")
    provider_id = str(p.get("id", ""))
    transaction = p.get("transaction") or {}
    paysuite_status = transaction.get("status", "pending")
    internal_status = _resolve_paysuite_status(paysuite_status)

    amount_raw = p.get("amount", 0)
    try:
        amount = int(float(amount_raw))
    except Exception:
        amount = 0

    existing = db.get_payment_by_reference(reference)

    if existing:
        if existing["status"] != internal_status:
            db.update_payment(reference, status=internal_status, provider_payment_id=provider_id)
            existing = db.get_payment_by_reference(reference)
        payment = existing
        created = False
    else:
        # Paysuite does NOT return payer mobile/name in payment responses.
        # Extract name from description if present.
        description = p.get("description", "")
        name = description.replace("English Buddy registration for ", "").strip() or "Unknown"
        method = p.get("method", "mpesa")
        checkout_url = p.get("checkout_url")

        try:
            payment = db.create_payment(
                name=name,
                mobile="",  # Paysuite does not expose payer phone on payments
                reference=reference,
                amount=amount,
                method=method,
                status=internal_status,
                provider_payment_id=provider_id,
                checkout_url=checkout_url,
            )
        except ValueError:
            payment = db.get_payment_by_reference(reference)
        created = True

    # Activate user if successful
    if internal_status == "success":
        mobile = payment.get("mobile", "")
        if mobile:
            user = db.get_user_by_mobile(mobile)
            if not user:
                db.create_user(name=payment["name"], mobile=mobile, is_paid=True)
            elif not user.get("is_paid"):
                db.update_user(user["id"], is_paid=True, is_active=True)

    return {"reference": reference, "status": internal_status, "created": created}


@app.delete("/api/admin/payments/clear-pending")
def clear_pending_payments(admin: dict = Depends(get_admin_user)):
    deleted = db.delete_payments_by_status("pending")
    return {"ok": True, "deleted": deleted, "message": f"Removed {deleted} pending payment(s)"}


@app.post("/api/admin/payments/{reference}/reject")
def reject_payment(reference: str, admin: dict = Depends(get_admin_user)):
    """Mark a payment as rejected (denied by admin)."""
    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    updated = db.update_payment(reference, status="rejected")
    return {"ok": True, "message": "Payment rejected.", "payment": updated}


@app.delete("/api/admin/payments/{reference}")
def delete_payment(reference: str, admin: dict = Depends(get_admin_user)):
    """Permanently delete a single payment record."""
    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    db.delete_payment_by_reference(reference)
    return {"ok": True, "message": f"Payment {reference} deleted."}


@app.post("/api/admin/import-by-paysuite-id/{paysuite_id}")
def import_by_paysuite_id(paysuite_id: str, admin: dict = Depends(get_admin_user)):
    """Look up a payment directly by Paysuite's internal ID and import it into the DB."""
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    headers = {
        "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
        "Accept": "application/json"
    }

    try:
        resp = requests.get(
            f"{PAYSUITE_API_BASE}/payments/{paysuite_id}",
            headers=headers,
            timeout=30
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Paysuite request failed: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Payment not found on Paysuite")

    data = resp.json().get("data", {})
    if not data:
        raise HTTPException(status_code=404, detail="Empty response from Paysuite")

    # Determine status from transaction
    transaction = data.get("transaction") or {}
    paysuite_status = transaction.get("status", "pending")
    internal_status = _resolve_paysuite_status(paysuite_status)

    reference = data.get("reference", "")
    amount_raw = data.get("amount", 0)
    try:
        amount = int(float(amount_raw))
    except Exception:
        amount = 0

    # Check if already in DB
    existing = db.get_payment_by_reference(reference)
    if existing:
        db.update_payment(reference, status=internal_status, provider_payment_id=paysuite_id)
    else:
        # Create from Paysuite data — name/mobile unknown so use description
        description = data.get("description", "")
        name = description.replace("English Buddy registration for ", "").strip() or "Unknown"
        method = data.get("method", "mpesa")
        try:
            db.create_payment(
                name=name, mobile="", reference=reference, amount=amount,
                method=method, status=internal_status, provider_payment_id=paysuite_id,
                checkout_url=data.get("checkout_url")
            )
        except ValueError:
            db.update_payment(reference, status=internal_status, provider_payment_id=paysuite_id)

    payment = db.get_payment_by_reference(reference)

    # Activate user if success and mobile is known
    if internal_status == "success" and payment.get("mobile"):
        user = db.get_user_by_mobile(payment["mobile"])
        if not user:
            db.create_user(name=payment["name"], mobile=payment["mobile"], is_paid=True)
        elif not user.get("is_paid"):
            db.update_user(user["id"], is_paid=True, is_active=True)

    return {
        "ok": True,
        "status": internal_status,
        "reference": reference,
        "message": f"Payment {reference} — status: {internal_status}",
        "note": "If mobile was unknown, user was not auto-created. Set mobile manually in Users tab." if not payment.get("mobile") else ""
    }


@app.post("/api/admin/sync-all-pending")
def sync_all_pending(admin: dict = Depends(get_admin_user)):
    """
    Re-check every pending payment that has a provider_payment_id against Paysuite.
    Paysuite only supports lookup by their internal ID — listing is not available.
    """
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    headers = {
        "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
        "Accept": "application/json"
    }

    all_payments = db.get_all_payments()
    pending = [p for p in all_payments if p["status"] in ("pending", "failed") and p.get("provider_payment_id")]
    confirmed, still_pending, errors = 0, 0, 0

    for payment in pending:
        try:
            resp = requests.get(
                f"{PAYSUITE_API_BASE}/payments/{payment['provider_payment_id']}",
                headers=headers,
                timeout=30
            )
            if resp.status_code != 200:
                errors += 1
                continue

            data = resp.json().get("data", {})
            transaction = data.get("transaction") or {}
            paysuite_status = transaction.get("status", "pending")

            if paysuite_status in ("completed", "paid"):
                db.update_payment(payment["reference"], status="success")
                payment = db.get_payment_by_reference(payment["reference"])
                existing_user = db.get_user_by_mobile(payment["mobile"])
                if not existing_user:
                    db.create_user(name=payment["name"], mobile=payment["mobile"], is_paid=True,
                                   affiliate_code=payment.get("affiliate_code"))
                    update_affiliate_stats(payment)
                else:
                    db.update_user(existing_user["id"], is_paid=True, is_active=True)
                    if not payment.get("commission_paid"):
                        update_affiliate_stats(payment)
                confirmed += 1
            elif paysuite_status in ("failed", "cancelled"):
                db.update_payment(payment["reference"], status=paysuite_status)
                errors += 1
            else:
                still_pending += 1

        except Exception as e:
            print(f"[SYNC] Error on {payment['reference']}: {e}")
            errors += 1

    return {
        "ok": True,
        "message": f"Sync complete: {confirmed} confirmed, {still_pending} still pending, {errors} errors/failed",
        "confirmed": confirmed,
        "still_pending": still_pending,
        "errors": errors
    }


# =====================================================
# ADMIN - AFFILIATE MANAGEMENT
# =====================================================

@app.get("/api/admin/affiliates")
def get_all_affiliates(admin: dict = Depends(get_admin_user)):
    affiliates = db.get_all_affiliates()
    return {
        "ok": True,
        "affiliates": [
            {
                "id": a["id"],
                "code": a["code"],
                "name": a["name"],
                "mobile": a["mobile"],
                "commission_rate": a["commission_rate"],
                "total_referrals": a["total_referrals"],
                "total_earnings": a["total_earnings"],
                "is_active": a["is_active"],
                "created_at": a["created_at"]
            }
            for a in affiliates
        ]
    }


@app.post("/api/admin/affiliates")
def create_affiliate(payload: CreateAffiliatePayload, admin: dict = Depends(get_admin_user)):
    code = payload.code.strip().upper()
    name = payload.name.strip()
    mobile = payload.mobile.strip() if payload.mobile else None

    if not code or not name:
        raise HTTPException(status_code=400, detail="Code and name are required")

    if not mobile:
        raise HTTPException(status_code=400, detail="Mobile number is required for affiliate payouts")

    existing = db.get_affiliate_by_code(code)
    if existing:
        raise HTTPException(status_code=400, detail="Affiliate code already exists")

    try:
        # Create affiliate without password initially
        # Admin can set password later via set-password endpoint
        affiliate = db.create_affiliate(
            code=code,
            name=name,
            mobile=payload.mobile,
            commission_rate=payload.commission_rate,
            password=None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "ok": True,
        "message": "Affiliate created successfully. Please set a password for them.",
        "affiliate": {
            "id": affiliate["id"],
            "code": affiliate["code"],
            "name": affiliate["name"],
            "mobile": affiliate["mobile"],
            "commission_rate": affiliate["commission_rate"],
            "has_password": False
        }
    }


@app.put("/api/admin/affiliates/{affiliate_id}")
def update_affiliate(affiliate_id: int, payload: UpdateAffiliatePayload, admin: dict = Depends(get_admin_user)):
    affiliate = db.get_affiliate_by_id(affiliate_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    updates = {}
    if payload.name is not None:
        updates["name"] = payload.name.strip()
    if payload.mobile is not None:
        updates["mobile"] = payload.mobile
    if payload.commission_rate is not None:
        updates["commission_rate"] = payload.commission_rate
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active

    affiliate = db.update_affiliate(affiliate_id, **updates)
    return {
        "ok": True,
        "message": "Affiliate updated successfully",
        "affiliate": {
            "id": affiliate["id"],
            "code": affiliate["code"],
            "name": affiliate["name"],
            "mobile": affiliate["mobile"],
            "commission_rate": affiliate["commission_rate"],
            "is_active": affiliate["is_active"]
        }
    }


@app.post("/api/admin/affiliates/{affiliate_id}/set-password")
def set_affiliate_password(affiliate_id: int, payload: SetAffiliatePasswordPayload, admin: dict = Depends(get_admin_user)):
    """Set or reset affiliate password (admin only)"""
    affiliate = db.get_affiliate_by_id(affiliate_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    if not payload.password or len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    hashed_password = hash_password(payload.password)
    db.update_affiliate(affiliate_id, password=hashed_password, password_reset_required=False)

    return {
        "ok": True,
        "message": f"Password set successfully for {affiliate['name']}",
        "affiliate_code": affiliate["code"]
    }


@app.get("/api/affiliate/dashboard")
def get_affiliate_dashboard(authorization: str = Header(default="")):
    """Affiliate's own dashboard data — uses affiliate token, no admin required."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization[7:]
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        affiliate_code = data.get("affiliate_code")
        if not affiliate_code or data.get("role") != "affiliate":
            raise HTTPException(status_code=403, detail="Not an affiliate account")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    affiliate = db.get_affiliate_by_code(affiliate_code)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    users = db.get_users_by_affiliate(affiliate_code)
    payments = db.get_payments_by_affiliate(affiliate_code, status="success")
    payouts = db.get_payouts_by_affiliate(affiliate["id"])

    return {
        "ok": True,
        "affiliate": {
            "id": affiliate["id"],
            "code": affiliate["code"],
            "name": affiliate["name"],
            "mobile": affiliate["mobile"],
            "commission_rate": affiliate.get("commission_rate", 20),
            "total_referrals": affiliate["total_referrals"],
            "total_earnings": affiliate["total_earnings"]
        },
        "users": [{"name": u["name"], "mobile": u["mobile"], "created_at": u["created_at"]} for u in users],
        "payments": [
            {
                "reference": p["reference"],
                "amount": p["amount"],
                "commission_amount": p["commission_amount"],
                "commission_paid": p["commission_paid"],
                "created_at": p["created_at"]
            }
            for p in payments
        ],
        "payouts": [
            {
                "id": p["id"],
                "amount": p["amount"],
                "method": p["method"],
                "notes": p.get("notes"),
                "paid_by": p.get("paid_by"),
                "created_at": p["created_at"]
            }
            for p in payouts
        ]
    }


@app.get("/api/admin/affiliates/{affiliate_code}/referrals")
def get_affiliate_referrals(affiliate_code: str, admin: dict = Depends(get_admin_user)):
    affiliate = db.get_affiliate_by_code(affiliate_code)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    users = db.get_users_by_affiliate(affiliate_code)
    payments = db.get_payments_by_affiliate(affiliate_code, status="success")
    payouts = db.get_payouts_by_affiliate(affiliate["id"])

    return {
        "ok": True,
        "affiliate": {
            "id": affiliate["id"],
            "code": affiliate["code"],
            "name": affiliate["name"],
            "mobile": affiliate["mobile"],
            "total_referrals": affiliate["total_referrals"],
            "total_earnings": affiliate["total_earnings"]
        },
        "users": [{"name": u["name"], "mobile": u["mobile"], "created_at": u["created_at"]} for u in users],
        "payments": [
            {
                "reference": p["reference"],
                "amount": p["amount"],
                "commission_amount": p["commission_amount"],
                "commission_paid": p["commission_paid"],
                "created_at": p["created_at"]
            }
            for p in payments
        ],
        "payouts": [
            {
                "id": p["id"],
                "amount": p["amount"],
                "method": p["method"],
                "notes": p.get("notes"),
                "paid_by": p.get("paid_by"),
                "created_at": p["created_at"]
            }
            for p in payouts
        ]
    }


# =====================================================
# ADMIN - PAYOUT MANAGEMENT
# =====================================================

@app.post("/api/admin/payouts")
def create_payout(payload: CreatePayoutPayload, admin: dict = Depends(get_admin_user)):
    affiliate = db.get_affiliate_by_id(payload.affiliate_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    if not affiliate.get("mobile"):
        raise HTTPException(status_code=400, detail=f"Affiliate {affiliate['name']} has no mobile number configured")

    if not payload.amount or payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Payout amount must be greater than 0")

    if payload.amount > affiliate["total_earnings"]:
        raise HTTPException(status_code=400, detail=f"Payout amount ({payload.amount} MZN) exceeds available earnings ({affiliate['total_earnings']} MZN)")

    if payload.amount < 10:
        raise HTTPException(status_code=400, detail="Minimum payout amount is 10 MZN")

    if payload.amount > 1000000:
        raise HTTPException(status_code=400, detail="Maximum payout amount is 1,000,000 MZN")

    try:
        payout_reference = f"PO{affiliate['code'][:8].upper()}{uuid.uuid4().hex[:10].upper()}"[:30]
        print(f"[PAYOUT] Initiating PaySuite payout for {affiliate['name']} - {payload.amount} MZN")

        paysuite_result = payout_service.create_payout(
            amount=payload.amount,
            mobile=affiliate["mobile"],
            holder_name=affiliate["name"],
            reference=payout_reference,
            description=payload.notes or f"Affiliate commission payout for {affiliate['code']}",
            method=payload.method
        )

        if paysuite_result["success"]:
            print(f"[PAYOUT] PaySuite payout created: {paysuite_result['payout_id']}")
            payout = db.create_payout(
                affiliate_id=payload.affiliate_id,
                amount=payload.amount,
                method=payload.method,
                notes=payload.notes,
                paid_by=admin["name"],
                provider_payout_id=paysuite_result["payout_id"],
                provider_reference=paysuite_result["reference"],
                status="pending"
            )
            return {
                "ok": True,
                "message": f"Payout of {payload.amount} MZN initiated for {affiliate['name']}",
                "payout": payout,
                "provider_status": paysuite_result["status"]
            }
        else:
            error_msg = paysuite_result.get("message", "Unknown error")
            print(f"[PAYOUT] PaySuite error: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Payout failed: {error_msg}")

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"[PAYOUT] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Payout error: {str(e)}")


@app.get("/api/admin/payouts")
def get_all_payouts(admin: dict = Depends(get_admin_user)):
    payouts = db.get_all_payouts()
    return {
        "ok": True,
        "payouts": [
            {
                "id": p["id"],
                "affiliate_id": p["affiliate_id"],
                "affiliate_code": p["affiliate_code"],
                "affiliate_name": p["affiliate_name"],
                "amount": p["amount"],
                "method": p["method"],
                "status": p["status"],
                "provider_payout_id": p.get("provider_payout_id"),
                "provider_reference": p.get("provider_reference"),
                "notes": p.get("notes"),
                "paid_by": p.get("paid_by"),
                "created_at": p["created_at"],
                "updated_at": p.get("updated_at", p["created_at"])
            }
            for p in payouts
        ]
    }


@app.post("/api/admin/payouts/{payout_id}/check-status")
def check_payout_status(payout_id: int, admin: dict = Depends(get_admin_user)):
    payout = db.get_payout_by_id(payout_id)
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")

    provider_payout_id = payout.get("provider_payout_id")
    if not provider_payout_id:
        raise HTTPException(status_code=400, detail="This payout was not created via PaySuite API")

    print(f"[PAYOUT] Checking status for payout {payout_id} (PaySuite ID: {provider_payout_id})")
    status_result = payout_service.check_payout_status(provider_payout_id)

    if status_result["success"]:
        new_status = status_result["status"]
        old_status = payout["status"]

        if new_status != old_status:
            print(f"[PAYOUT] Status changed: {old_status} -> {new_status}")
            updated_payout = db.update_payout(payout_id=payout_id, status=new_status)
            return {
                "ok": True,
                "message": f"Payout status updated to '{new_status}'",
                "payout": updated_payout,
                "previous_status": old_status
            }
        else:
            return {
                "ok": True,
                "message": f"Payout status is still '{new_status}'",
                "payout": payout,
                "no_change": True
            }
    else:
        error_msg = status_result.get("message", "Unknown error")
        print(f"[PAYOUT] Status check error: {error_msg}")
        return {
            "ok": False,
            "error": status_result.get("error"),
            "message": f"Could not check status: {error_msg}",
            "payout": payout
        }


@app.get("/api/admin/payouts/{affiliate_id}")
def get_affiliate_payouts(affiliate_id: int, admin: dict = Depends(get_admin_user)):
    payouts = db.get_payouts_by_affiliate(affiliate_id)
    return {"ok": True, "payouts": payouts}


@app.post("/api/admin/affiliates/{affiliate_id}/add-credit")
def add_affiliate_credit(affiliate_id: int, payload: AddCreditPayload, admin: dict = Depends(get_admin_user)):
    affiliate = db.get_affiliate_by_id(affiliate_id)
    if not affiliate:
        raise HTTPException(status_code=404, detail="Affiliate not found")

    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Credit amount must be greater than 0")

    try:
        updated_affiliate = db.add_affiliate_credit(affiliate_id, payload.amount)
        return {
            "ok": True,
            "message": f"Added {payload.amount} MZN credit to {affiliate['name']}",
            "affiliate": updated_affiliate,
            "reason": payload.reason
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# =====================================================
# ADMIN - PAYMENT MANAGEMENT
# =====================================================

@app.post("/api/admin/payments/{reference}/complete")
def manually_complete_payment(reference: str, admin: dict = Depends(get_admin_user)):
    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment["status"] == "success":
        return {
            "ok": True,
            "message": "Payment is already marked as successful",
            "payment": payment
        }

    db.update_payment(reference, status="success")
    payment = db.get_payment_by_reference(reference)

    existing_user = db.get_user_by_mobile(payment["mobile"])

    if not existing_user:
        user_created = db.create_user(
            name=payment["name"],
            mobile=payment["mobile"],
            is_paid=True,
            affiliate_code=payment.get("affiliate_code")
        )
        print(f"User created after manual payment completion: {payment['mobile']}")
        update_affiliate_stats(payment)
    else:
        db.update_user(
            existing_user["id"],
            is_paid=True,
            is_active=True,
            affiliate_code=existing_user.get("affiliate_code") or payment.get("affiliate_code")
        )
        user_created = db.get_user_by_id(existing_user["id"])
        print(f"Existing user marked as paid: {payment['mobile']}")
        if not payment.get("commission_paid"):
            update_affiliate_stats(payment)

    # Notify the user
    target_user = user_created if isinstance(user_created, dict) else db.get_user_by_mobile(payment["mobile"])
    if target_user:
        db.create_notification(
            title="Pagamento confirmado!",
            body=f"O seu pagamento de {payment.get('amount', '')} MZN foi confirmado. Bem-vindo ao English Buddy!",
            notif_type="success",
            user_id=target_user["id"],
        )

    return {
        "ok": True,
        "message": "Payment marked as successful",
        "payment": payment,
        "user": user_created
    }


@app.get("/api/admin/payments/pending")
def get_pending_payments(admin: dict = Depends(get_admin_user)):
    all_payments = db.get_all_payments()
    pending_payments = [p for p in all_payments if p["status"] == "pending"]
    return {
        "ok": True,
        "count": len(pending_payments),
        "payments": [
            {
                "reference": p["reference"],
                "name": p["name"],
                "mobile": p["mobile"],
                "amount": p["amount"],
                "method": p["method"],
                "status": p["status"],
                "checkout_url": p.get("checkout_url"),
                "created_at": p["created_at"]
            }
            for p in pending_payments
        ]
    }


@app.get("/api/payments/{reference}/status")
def get_payment_status_local(reference: str):
    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    user = db.get_user_by_mobile(payment["mobile"])
    token = None
    if payment["status"] == "success" and user and user.get("is_paid"):
        token = create_token(user)
    return {
        "ok": True,
        "payment": {
            "reference": payment["reference"],
            "name": payment["name"],
            "mobile": payment["mobile"],
            "amount": payment["amount"],
            "method": payment["method"],
            "status": payment["status"],
            "checkout_url": payment.get("checkout_url"),
            "created_at": payment["created_at"]
        },
        "user_exists": user is not None,
        "user_paid": user["is_paid"] if user else False,
        "token": token
    }


# =====================================================
# ADMIN - PROMOTIONS
# =====================================================

class CreatePromotionPayload(BaseModel):
    name: str
    description: str = ""
    extra_days: int
    start_date: str   # ISO date string e.g. "2026-04-01T00:00:00+00:00"
    end_date: str

class UpdatePromotionPayload(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    extra_days: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_active: Optional[bool] = None

@app.get("/api/admin/promotions")
def list_promotions(admin: dict = Depends(get_admin_user)):
    return {"ok": True, "promotions": db.get_all_promotions()}

@app.post("/api/admin/promotions")
def create_promotion(payload: CreatePromotionPayload, admin: dict = Depends(get_admin_user)):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if payload.extra_days < 1:
        raise HTTPException(status_code=400, detail="extra_days must be at least 1")
    promo = db.create_promotion(
        name=payload.name.strip(),
        description=payload.description.strip(),
        extra_days=payload.extra_days,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    return {"ok": True, "promotion": promo}

@app.put("/api/admin/promotions/{promo_id}")
def update_promotion(promo_id: int, payload: UpdatePromotionPayload, admin: dict = Depends(get_admin_user)):
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    promo = db.update_promotion(promo_id, **updates)
    if not promo:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return {"ok": True, "promotion": promo}

@app.delete("/api/admin/promotions/{promo_id}")
def delete_promotion(promo_id: int, admin: dict = Depends(get_admin_user)):
    deleted = db.delete_promotion(promo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Promotion not found")
    return {"ok": True, "message": "Promotion deleted"}

@app.get("/api/promotions/active")
def get_active_promotions():
    """Public endpoint — Flutter can check active promos without auth."""
    return {"ok": True, "promotions": db.get_active_promotions()}


# =====================================================
# DATABASE MIGRATION
# =====================================================

@app.post("/api/admin/migrate-to-postgres")
async def migrate_to_postgres(request: Request, admin: dict = Depends(get_admin_user)):
    """
    One-time migration: POST your database.json content as the request body.
    This loads the snapshot into PostgreSQL so no data is lost.
    """
    if not db._use_postgres:
        raise HTTPException(status_code=400, detail="DATABASE_URL not set — not using PostgreSQL")
    try:
        snapshot = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    db.load_snapshot(snapshot)
    return {"ok": True, "message": "Migration complete. Data loaded into PostgreSQL."}


# =====================================================
# DEBUG ROUTES
# =====================================================

@app.get("/api/database/users")
def get_users():
    users = db.get_all_users()
    return {
        "ok": True,
        "data": [
            {
                "id": u["id"],
                "name": u["name"],
                "mobile": u["mobile"],
                "role": u.get("role", "student"),
                "is_paid": u["is_paid"],
                "is_active": u["is_active"],
                "is_banned": u.get("is_banned", False),
                "affiliate_code": u.get("affiliate_code"),
                "created_at": u["created_at"]
            }
            for u in users
        ]
    }


class UpdateUserPayload(BaseModel):
    role: Optional[str] = None
    is_banned: Optional[bool] = None
    extra_days: Optional[int] = None   # extra access days granted by admin


class ManualRegisterPayload(BaseModel):
    name: str
    mobile: str
    is_paid: bool = True
    role: str = "student"
    affiliate_code: Optional[str] = None
    extra_days: int = 0


class UpdatePaymentAmountPayload(BaseModel):
    amount: int


@app.post("/api/admin/users/register")
def admin_manual_register(payload: ManualRegisterPayload, admin: dict = Depends(get_admin_user)):
    mobile = process_mobile_number(payload.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")
    existing = db.get_user_by_mobile(mobile)
    if existing:
        raise HTTPException(status_code=409, detail="A user with this mobile already exists")
    user = db.create_user(
        name=payload.name.strip(),
        mobile=mobile,
        is_paid=payload.is_paid,
        role=payload.role,
        affiliate_code=payload.affiliate_code or None,
    )
    if payload.extra_days > 0:
        db.update_user(user["id"], extra_days=payload.extra_days)
        user = db.get_user_by_id(user["id"])
    return {"ok": True, "user": user}


@app.put("/api/admin/payments/{reference}/amount")
def admin_update_payment_amount(reference: str, payload: UpdatePaymentAmountPayload,
                                admin: dict = Depends(get_admin_user)):
    if payload.amount < 1:
        raise HTTPException(status_code=400, detail="Amount must be at least 1")
    payment = db.get_payment_by_reference(reference)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    updated = db.update_payment(reference, amount=payload.amount)
    return {"ok": True, "payment": updated}


@app.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, payload: UpdateUserPayload, admin: dict = Depends(get_admin_user)):
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updates = {}
    if payload.role is not None:
        if payload.role not in ("student", "admin", "affiliate"):
            raise HTTPException(status_code=400, detail="Invalid role")
        updates["role"] = payload.role
    if payload.is_banned is not None:
        updates["is_banned"] = payload.is_banned
        updates["is_active"] = not payload.is_banned
    if payload.extra_days is not None:
        if payload.extra_days < 0:
            raise HTTPException(status_code=400, detail="extra_days cannot be negative")
        updates["extra_days"] = payload.extra_days

    updated = db.update_user(user_id, **updates)
    return {"ok": True, "user": updated}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, admin: dict = Depends(get_admin_user)):
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("role") == "admin":
        raise HTTPException(status_code=403, detail="Cannot delete admin account")
    deleted = db.delete_user(user_id)
    return {"ok": True, "deleted": deleted}


@app.get("/api/database/payments")
def get_payments():
    payments = db.get_all_payments()
    return {
        "ok": True,
        "data": [
            {
                "id": p["id"],
                "name": p["name"],
                "mobile": p["mobile"],
                "reference": p["reference"],
                "amount": p["amount"],
                "method": p["method"],
                "status": p["status"],
                "provider_payment_id": p["provider_payment_id"],
                "provider_reference": p["provider_reference"],
                "checkout_url": p["checkout_url"],
                "created_at": p["created_at"]
            }
            for p in payments
        ]
    }


@app.get("/api/test/database-status")
def test_database_status():
    users = db.get_all_users()
    return {
        "ok": True,
        "database_type": "JSON",
        "database_file": "database.json",
        "users_count": len(users),
        "users": [
            {"id": u["id"], "name": u["name"], "mobile": u["mobile"], "is_paid": u["is_paid"]}
            for u in users
        ]
    }


# =====================================================
# SPEECH PRACTICE
# =====================================================

from speech_service import transcribe_audio, evaluate_text, chat_reply, CONVERSATIONS, get_random_topic, analyze_session
from fastapi import UploadFile, File

PRACTICE_BASE_DAYS    = 90    # standard access: 3 months
EARLY_USER_LIMIT      = 200   # first N users get double access days
EARLY_USER_DAYS       = 180   # 6 months for early users (double)


def _check_practice_access(user: dict) -> dict:
    """
    Access rules:
    - First 200 users: 180 days (6 months)
    - Everyone else:    90 days (3 months)
    - Active promotions can extend access further.
    """
    now = datetime.now(timezone.utc)
    created_at = user.get("created_at", "")
    try:
        registered = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        registered = now

    is_early_user = user["id"] <= EARLY_USER_LIMIT
    base_days = EARLY_USER_DAYS if is_early_user else PRACTICE_BASE_DAYS
    base_days += int(user.get("extra_days") or 0)   # admin-granted bonus days
    expiry = registered + timedelta(days=base_days)

    # Check active promotions — if ANY promo is active, ALL users get access for its duration
    promos = db.get_active_promotions()
    active_promo = None
    promo_end_date = None
    for promo in promos:
        try:
            end = datetime.fromisoformat(promo["end_date"].replace("Z", "+00:00"))
            if promo_end_date is None or end > promo_end_date:
                promo_end_date = end
                active_promo = promo
        except Exception:
            continue

    # Active promo overrides expiry for ALL users — everyone gets access until promo ends
    if active_promo and promo_end_date:
        expiry = max(expiry, promo_end_date)

    allowed = now < expiry
    days_remaining = max(0, (expiry - now).days)

    if active_promo and is_early_user:
        reason = f"Early user + promo '{active_promo['name']}' — {days_remaining} days left."
    elif active_promo:
        reason = f"Promo '{active_promo['name']}' active — {days_remaining} days left."
    elif allowed and is_early_user:
        reason = f"Early user bonus: {days_remaining} days of practice remaining."
    elif allowed:
        reason = f"Practice available for {days_remaining} more days."
    else:
        reason = "Your free practice access has expired."

    return {
        "allowed": allowed,
        "days_remaining": days_remaining,
        "expiry_date": expiry.isoformat(),
        "is_early_user": is_early_user,
        "active_promo": active_promo["name"] if active_promo else None,
        "reason": reason,
    }


class EvaluateTextPayload(BaseModel):
    text: str
    conversation_id: Optional[int] = None

class ChatPayload(BaseModel):
    message: str
    conversation_id: Optional[int] = None
    history: list = Field(default_factory=list)


@app.get("/api/practice/access")
def get_practice_access(user: dict = Depends(get_current_user)):
    """Let Flutter check access status before showing the practice screen."""
    return {"ok": True, **_check_practice_access(user)}


@app.get("/api/practice/conversations")
def get_conversations(user: dict = Depends(get_current_user)):
    access = _check_practice_access(user)
    if not access["allowed"]:
        raise HTTPException(status_code=403, detail=access["reason"])
    return {"ok": True, "conversations": CONVERSATIONS}


@app.post("/api/practice/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user)
):
    access = _check_practice_access(user)
    if not access["allowed"]:
        raise HTTPException(status_code=403, detail=access["reason"])
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    try:
        audio_bytes = await file.read()
        transcript = transcribe_audio(audio_bytes, filename=file.filename or "audio.m4a")
        return {"ok": True, "transcript": transcript}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/practice/evaluate")
def evaluate(payload: EvaluateTextPayload, user: dict = Depends(get_current_user)):
    access = _check_practice_access(user)
    if not access["allowed"]:
        raise HTTPException(status_code=403, detail=access["reason"])
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text is required")

    try:
        feedback = evaluate_text(payload.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Find conversation context
    conversation_id = payload.conversation_id or 0
    scenario = ""
    question = ""
    if conversation_id:
        conv = next((c for c in CONVERSATIONS if c["id"] == conversation_id), None)
        if conv:
            scenario = conv["scenario"]
            question = conv["question"]

    # Save session — DB handles base XP; double it here if eligible
    session = db.save_practice_session(
        user_id=user["id"],
        conversation_id=conversation_id,
        scenario=scenario,
        question=question,
        transcript=payload.text,
        corrected=feedback.get("corrected", ""),
        grammar=feedback.get("grammar", ""),
        pronunciation=feedback.get("pronunciation", ""),
        examples=feedback.get("examples", []),
        grammar_score=feedback.get("grammar_score", 5),
        pronunciation_score=feedback.get("pronunciation_score", 5),
        double_xp=False,
    )

    progress = db.get_user_progress(user["id"])

    return {
        "ok": True,
        "feedback": {
            "corrected": feedback.get("corrected", ""),
            "grammar": feedback.get("grammar", ""),
            "pronunciation": feedback.get("pronunciation", ""),
            "examples": feedback.get("examples", []),
            "grammar_score": feedback.get("grammar_score", 5),
            "pronunciation_score": feedback.get("pronunciation_score", 5),
        },
        "xp_earned": session["xp_earned"],
        "access": {
            "days_remaining": access["days_remaining"],
            "expiry_date": access["expiry_date"],
            "is_early_user": access["is_early_user"],
            "active_promo": access["active_promo"],
            "reason": access["reason"],
        },
        "progress": {
            "xp": progress["xp"],
            "level": progress["level"],
            "next_level": progress["next_level"],
            "xp_to_next": progress["xp_to_next"],
            "total_sessions": progress["total_sessions"],
        }
    }


@app.post("/api/practice/chat")
def practice_chat(payload: ChatPayload, user: dict = Depends(get_current_user)):
    """Conversational AI practice — GPT replies and gently corrects."""
    access = _check_practice_access(user)
    if not access["allowed"]:
        raise HTTPException(status_code=403, detail=access["reason"])
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")

    # Keep last 10 turns for context, append current user message
    history = list(payload.history[-10:])
    history.append({"role": "user", "content": payload.message})

    try:
        result = chat_reply(history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    conversation_id = payload.conversation_id or 0
    scenario, question = "", ""
    if conversation_id:
        conv = next((c for c in CONVERSATIONS if c["id"] == conversation_id), None)
        if conv:
            scenario = conv["scenario"]
            question = conv["question"]

    session = db.save_practice_session(
        user_id=user["id"],
        conversation_id=conversation_id,
        scenario=scenario,
        question=question,
        transcript=payload.message,
        corrected=result.get("correction") or payload.message,
        grammar="",
        pronunciation="",
        examples=[],
        grammar_score=7,
        pronunciation_score=7,
        double_xp=False,
    )

    progress = db.get_user_progress(user["id"])
    return {
        "ok": True,
        "reply": result.get("reply", ""),
        "correction": result.get("correction"),
        "tip": result.get("tip"),
        "explanation": result.get("explanation"),
        "xp_earned": session["xp_earned"],
        "progress": {
            "xp": progress["xp"],
            "level": progress["level"],
            "xp_to_next": progress["xp_to_next"],
            "total_sessions": progress["total_sessions"],
        },
    }


@app.get("/api/practice/topic")
def get_topic(user: dict = Depends(get_current_user)):
    """Return a random topic matched to the user's current level."""
    _check_practice_access(user)
    progress = db.get_user_progress(user["id"])
    level = progress["level"] if progress else 1
    topic = get_random_topic(level)
    return {"ok": True, "topic": topic, "level": level}


class AnalyzePayload(BaseModel):
    history: list = Field(default_factory=list)


@app.post("/api/practice/analyze")
def analyze_practice(payload: AnalyzePayload, user: dict = Depends(get_current_user)):
    """Analyze a completed 2-minute session and return score + feedback."""
    _check_practice_access(user)
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    if not payload.history:
        raise HTTPException(status_code=400, detail="History is empty")
    try:
        result = analyze_session(payload.history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    progress = db.get_user_progress(user["id"])
    return {
        "ok": True,
        "score": result.get("score", 5),
        "strengths": result.get("strengths", []),
        "improvements": result.get("improvements", []),
        "tip": result.get("tip", ""),
        "tip_pt": result.get("tip_pt", ""),
        "turn_feedback": result.get("turn_feedback", []),
        "progress": {
            "xp": progress["xp"],
            "level": progress["level"],
            "xp_to_next": progress["xp_to_next"],
            "total_sessions": progress["total_sessions"],
        } if progress else {},
    }


@app.get("/api/practice/progress")
def get_progress(user: dict = Depends(get_current_user)):
    access = _check_practice_access(user)
    progress = db.get_user_progress(user["id"])
    if not progress:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "ok": True,
        "progress": progress,
        "access": {
            "allowed": access["allowed"],
            "days_remaining": access["days_remaining"],
            "expiry_date": access["expiry_date"],
            "double_xp": access["double_xp"],
            "reason": access["reason"],
        }
    }


@app.get("/api/practice/history")
def get_history(user: dict = Depends(get_current_user)):
    sessions = db.get_sessions_by_user(user["id"])
    return {
        "ok": True,
        "total": len(sessions),
        "sessions": [
            {
                "id": s["id"],
                "scenario": s["scenario"],
                "question": s["question"],
                "transcript": s["transcript"],
                "corrected": s["corrected"],
                "grammar_score": s["grammar_score"],
                "pronunciation_score": s["pronunciation_score"],
                "xp_earned": s["xp_earned"],
                "double_xp": s.get("double_xp", False),
                "created_at": s["created_at"]
            }
            for s in sessions
        ]
    }


# =====================================================
# NOTIFICATIONS
# =====================================================

class SendNotificationPayload(BaseModel):
    title: str
    body: str
    user_id: Optional[int] = None  # None = broadcast to all users
    type: str = "info"             # "info", "success", "warning", "danger"


@app.get("/api/notifications")
def get_my_notifications(user: dict = Depends(get_current_user)):
    notifs = db.get_notifications_for_user(user["id"])
    result = []
    for n in notifs:
        result.append({
            "id": n["id"],
            "title": n["title"],
            "body": n["body"],
            "type": n.get("type", "info"),
            "is_read": user["id"] in (n.get("read_by") or []),
            "created_at": n["created_at"],
        })
    return {"ok": True, "notifications": result, "unread_count": db.get_unread_count(user["id"])}


@app.post("/api/notifications/{notif_id}/read")
def mark_notification_read(notif_id: int, user: dict = Depends(get_current_user)):
    db.mark_notification_read(notif_id, user["id"])
    return {"ok": True}


@app.post("/api/admin/notifications/send")
def admin_send_notification(payload: SendNotificationPayload, admin: dict = Depends(get_admin_user)):
    notif = db.create_notification(
        title=payload.title,
        body=payload.body,
        notif_type=payload.type,
        user_id=payload.user_id,
    )
    target = f"user #{payload.user_id}" if payload.user_id else "all users"
    return {"ok": True, "message": f"Notification sent to {target}.", "notification": notif}


@app.get("/api/admin/notifications/priority")
def admin_priority_notifications(admin: dict = Depends(get_admin_user)):
    """Returns priority alerts for the admin dashboard: pending payments + expiring users."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)

    # Pending payments
    pending = [p for p in db.get_all_payments() if p["status"] == "pending"]

    # Users expiring within 7 days
    users = db.get_all_users()
    expiring_soon = []
    for u in users:
        if not u.get("is_paid"):
            continue
        try:
            registered = datetime.fromisoformat(u["created_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        is_early = u["id"] <= 200
        base_days = 180 if is_early else 90
        base_days += int(u.get("extra_days") or 0)
        expiry = registered + timedelta(days=base_days)
        days_left = (expiry - now).days
        if 0 <= days_left <= 7:
            expiring_soon.append({
                "id": u["id"], "name": u["name"], "mobile": u["mobile"],
                "days_left": days_left, "expiry_date": expiry.isoformat(),
            })

    # Recent notifications (last 20)
    recent_notifs = db.get_all_notifications()[:20]

    return {
        "ok": True,
        "pending_payments_count": len(pending),
        "pending_payments": [{"reference": p["reference"], "name": p["name"], "mobile": p["mobile"], "amount": p["amount"], "created_at": p["created_at"]} for p in pending[:10]],
        "expiring_users": sorted(expiring_soon, key=lambda x: x["days_left"]),
        "recent_notifications": recent_notifs,
    }


@app.get("/api/admin/notifications")
def admin_get_notifications(admin: dict = Depends(get_admin_user)):
    return {"ok": True, "notifications": db.get_all_notifications()}


# =====================================================
# BACKGROUND POLLING
# =====================================================

async def _poll_pending_payments():
    """Re-check all pending payments every 5 minutes."""
    while True:
        await asyncio.sleep(5 * 60)
        if not PAYSUITE_API_TOKEN:
            continue

        all_payments = db.get_all_payments()
        pending = [p for p in all_payments if p["status"] == "pending" and p.get("provider_payment_id")]
        print(f"[POLL] Checking {len(pending)} pending payment(s)...")

        headers = {
            "Authorization": f"Bearer {PAYSUITE_API_TOKEN}",
            "Accept": "application/json"
        }

        for payment in pending:
            try:
                response = requests.get(
                    f"{PAYSUITE_API_BASE}/payments/{payment['provider_payment_id']}",
                    headers=headers,
                    timeout=30
                )
                if response.status_code != 200:
                    continue

                data = response.json().get("data", {})
                transaction = data.get("transaction") or {}
                paysuite_status = transaction.get("status", "pending")

                if paysuite_status in ("completed", "paid"):
                    internal_status = "success"
                elif paysuite_status in ("failed", "cancelled"):
                    internal_status = paysuite_status
                else:
                    continue  # still pending

                db.update_payment(payment["reference"], status=internal_status)
                payment = db.get_payment_by_reference(payment["reference"])
                print(f"[POLL] {payment['reference']} -> {internal_status}")

                if internal_status == "success":
                    existing_user = db.get_user_by_mobile(payment["mobile"])
                    if not existing_user:
                        db.create_user(
                            name=payment["name"],
                            mobile=payment["mobile"],
                            is_paid=True,
                            affiliate_code=payment.get("affiliate_code")
                        )
                        update_affiliate_stats(payment)
                    else:
                        db.update_user(existing_user["id"], is_paid=True, is_active=True)
                        if not payment.get("commission_paid"):
                            update_affiliate_stats(payment)
            except Exception as e:
                print(f"[POLL] Error on {payment['reference']}: {e}")

        print("[POLL] Done.")




if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8024))
    print(f"Starting FastAPI server on http://0.0.0.0:{port}")
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
