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
from pydantic import BaseModel

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

@asynccontextmanager
async def lifespan(_app: FastAPI):
    asyncio.create_task(_poll_pending_payments())
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# =====================================================
# REQUEST MODELS
# =====================================================

class LoginPayload(BaseModel):
    mobile: str

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
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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

@app.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/payment-return", response_class=HTMLResponse)
def payment_return_page(request: Request):
    return templates.TemplateResponse("payment_return.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/affiliate", response_class=HTMLResponse)
def affiliate_page(request: Request):
    return templates.TemplateResponse("affiliate.html", {"request": request})


# =====================================================
# AUTH
# =====================================================

@app.post("/api/login")
def login(payload: LoginPayload):
    mobile = process_mobile_number(payload.mobile)
    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    user = db.get_user_by_mobile(mobile)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user["is_paid"]:
        raise HTTPException(status_code=403, detail="Payment not completed")

    token = create_token(user)
    return {
        "ok": True,
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "mobile": user["mobile"]
        }
    }


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
def start_registration_payment(payload: StartPaymentPayload):
    if not PAYSUITE_API_TOKEN:
        raise HTTPException(status_code=500, detail="Missing Paysuite API token")

    mobile = process_mobile_number(payload.mobile)
    name = payload.name.strip()
    method = payload.method.strip().lower()
    affiliate_code = payload.affiliate_code.strip() if payload.affiliate_code else None

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    if not mobile:
        raise HTTPException(status_code=400, detail="Invalid mobile number")

    if method not in {"mpesa", "emola", "credit_card"}:
        raise HTTPException(status_code=400, detail="Invalid payment method")

    affiliate = None
    if affiliate_code:
        affiliate = db.get_affiliate_by_code(affiliate_code)
        if not affiliate or not affiliate["is_active"]:
            raise HTTPException(status_code=400, detail="Invalid affiliate code")

    existing_user = db.get_user_by_mobile(mobile)
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    reference = f"REG{int(datetime.now(timezone.utc).timestamp())}{mobile[-4:]}{uuid.uuid4().hex[:6].upper()}"

    body = {
        "amount": str(REGISTRATION_AMOUNT),
        "method": method,
        "reference": reference,
        "description": f"English Buddy registration for {name}",
        "return_url": RETURN_URL
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
    except requests.RequestException as e:
        print("Paysuite request failed:", str(e))
        raise HTTPException(status_code=502, detail=f"Paysuite request failed: {e}")
    except ValueError:
        print("Paysuite returned invalid JSON")
        raise HTTPException(status_code=502, detail="Paysuite returned invalid JSON")

    if response.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=provider.get("message", "Paysuite payment creation failed"))

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
            "status": payment["status"],
            "checkout_url": payment["checkout_url"]
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

    if not code or not name:
        raise HTTPException(status_code=400, detail="Code and name are required")

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
        "user_paid": user["is_paid"] if user else False
    }


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
                "is_paid": u["is_paid"],
                "is_active": u["is_active"],
                "created_at": u["created_at"]
            }
            for u in users
        ]
    }


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
# BACKGROUND POLLING
# =====================================================

async def _poll_pending_payments():
    """Re-check all pending payments every 2 hours."""
    while True:
        await asyncio.sleep(2 * 60 * 60)
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
    print("Starting FastAPI server on http://127.0.0.1:8024")
    uvicorn.run("app:app", host="0.0.0.0", port=8024, reload=False)
