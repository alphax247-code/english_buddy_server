# English Buddy – Flutter App API Guide

Base URL: `https://your-app.onrender.com`  
All requests that require auth must include:
```
Authorization: Bearer <jwt_token>
```

---

## 1. Environment Setup

Set your backend base URL in one place:
```dart
const String baseUrl = 'https://your-app.onrender.com';
```

---

## 2. Authentication

### Login (existing user)
```
POST /api/login
```
**Body:**
```json
{
  "mobile": "+258850219049"
}
```
**Response (success):**
```json
{
  "ok": true,
  "token": "<jwt>",
  "role": "student",
  "redirect": "/dashboard",
  "user": { "id": 1, "name": "John", "mobile": "+258850219049" }
}
```
**Error responses:**
| Status | Detail |
|--------|--------|
| 404 | `Number not registered. Please register first.` |
| 403 | `Payment not completed.` |
| 403 | `Your account has been suspended.` |

> Store the `token` in Flutter secure storage. Use it for all authenticated requests.

---

### Get Current User
```
GET /api/me
Authorization: Bearer <token>
```
**Response:**
```json
{
  "ok": true,
  "user": {
    "id": 1,
    "name": "John",
    "mobile": "+258850219049",
    "role": "student",
    "is_paid": true
  }
}
```

---

## 3. Registration & Payment Flow

This is the full flow for registering a new user:

```
User taps "Register" 
  → App calls /api/register/start-payment
  → Backend returns checkout_url
  → App opens checkout_url in browser/webview
  → User confirms payment on their phone
  → Paysuite redirects to: englishbuddy://payment/registration?reference=REG123
  → Android/iOS intercepts deep link → opens app
  → App calls /api/register/check-payment-status with reference
  → Backend confirms with Paysuite, creates user, returns JWT
  → App saves token → navigate to /home
```

### Step 1 — Start Payment
```
POST /api/register/start-payment
```
**Body:**
```json
{
  "mobile": "+258850219049",
  "name": "John Doe",
  "method": "mpesa",
  "affiliate_code": "BUDDY01"
}
```
> `affiliate_code` is optional. `method` options: `mpesa`, `emola`.

**Response:**
```json
{
  "ok": true,
  "message": "Payment started",
  "payment": {
    "reference": "REG17347935490496B8A26",
    "amount": 10,
    "status": "pending",
    "checkout_url": "https://checkout.paysuite.tech/..."
  }
}
```
> Open `checkout_url` in the browser. Save `reference` to check status later.

---

### Step 2 — Handle Deep Link

Configure your Android `AndroidManifest.xml`:
```xml
<intent-filter>
  <action android:name="android.intent.action.VIEW"/>
  <category android:name="android.intent.category.DEFAULT"/>
  <category android:name="android.intent.category.BROWSABLE"/>
  <data android:scheme="englishbuddy" android:host="payment"/>
</intent-filter>
```

In `main.dart`, handle the deep link:
```dart
// Deep link: englishbuddy://payment/registration?reference=REG123
void _handleDeepLink(Uri uri) {
  if (uri.host == 'payment' && uri.pathSegments.contains('registration')) {
    final reference = uri.queryParameters['reference'];
    if (reference != null) {
      confirmPayment(reference);
    }
  }
}
```

---

### Step 3 — Confirm Payment
```
POST /api/register/check-payment-status
```
**Body:**
```json
{
  "reference": "REG17347935490496B8A26"
}
```
**Response (confirmed):**
```json
{
  "ok": true,
  "status": "success",
  "message": "Payment confirmed",
  "token": "<jwt>",
  "user": { "id": 1, "name": "John Doe", "mobile": "+258850219049" }
}
```
**Response (still pending):**
```json
{
  "ok": true,
  "status": "pending",
  "message": "Payment status: pending"
}
```
> Poll this endpoint every few seconds while status is `pending`. Save `token` on success and navigate to home.

---

### Alternative — Check by Mobile
```
POST /api/register/check-payment-by-mobile
```
**Body:**
```json
{
  "mobile": "+258850219049"
}
```
> Use this if the user returns to the app without a reference (e.g. app was killed).

---

## 4. User Dashboard

### Get Payment History
```
GET /api/my-payments
Authorization: Bearer <token>
```
**Response:**
```json
{
  "ok": true,
  "payments": [
    {
      "id": 1,
      "reference": "REG123",
      "amount": 10,
      "method": "mpesa",
      "status": "success",
      "created_at": "2026-03-14T06:25:00Z"
    }
  ]
}
```

---

## 5. Affiliate Flow

### Affiliate Login
```
POST /api/affiliate/login
```
**Body:**
```json
{
  "code": "BUDDY01",
  "password": "their_password"
}
```
**Response:**
```json
{
  "ok": true,
  "token": "<jwt>",
  "user": {
    "id": 2,
    "name": "Jane",
    "role": "affiliate",
    "affiliate_code": "BUDDY01"
  },
  "password_reset_required": false
}
```

---

### Affiliate Dashboard
```
GET /api/affiliate/dashboard
Authorization: Bearer <affiliate_token>
```
**Response:**
```json
{
  "ok": true,
  "affiliate": {
    "code": "BUDDY01",
    "name": "Jane",
    "commission_rate": 20,
    "total_referrals": 5,
    "total_earnings": 100
  },
  "users": [
    { "name": "John", "mobile": "+258850000001", "created_at": "..." }
  ],
  "payments": [
    {
      "reference": "REG123",
      "amount": 10,
      "commission_amount": 2,
      "commission_paid": true,
      "created_at": "..."
    }
  ],
  "payouts": [
    {
      "id": 1,
      "amount": 20,
      "method": "mpesa",
      "notes": "March payout",
      "created_at": "..."
    }
  ]
}
```

---

### Register a User (Affiliate)
Affiliates use the same payment endpoint with their code attached:
```
POST /api/register/start-payment
Authorization: Bearer <affiliate_token>
```
**Body:**
```json
{
  "mobile": "+258850219049",
  "name": "New User",
  "method": "mpesa",
  "affiliate_code": "BUDDY01"
}
```
> Same flow as regular registration. Open `checkout_url`, user confirms, call `check-payment-status`.

---

## 6. Error Handling

All errors follow this format:
```json
{
  "detail": "Error message here"
}
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request (missing/invalid fields) |
| 401 | Not authenticated — clear token, redirect to login |
| 403 | Forbidden — account suspended or payment not complete |
| 404 | Resource not found |
| 502 | Paysuite API error |

In Flutter, handle 401 and 404 on `/api/me` by clearing the token and redirecting to login:
```dart
if (response.statusCode == 401 || response.statusCode == 404) {
  await storage.delete(key: 'token');
  Navigator.pushReplacementNamed(context, '/login');
}
```

---

## 7. Deep Link Setup (iOS)

Add to `ios/Runner/Info.plist`:
```xml
<key>CFBundleURLTypes</key>
<array>
  <dict>
    <key>CFBundleURLSchemes</key>
    <array>
      <string>englishbuddy</string>
    </array>
  </dict>
</array>
```

---

## 8. Payment Return URL

Make sure the `RETURN_URL` environment variable on Render is set to:
```
englishbuddy://payment/registration
```
Paysuite will append `?reference=REG123` automatically, giving:
```
englishbuddy://payment/registration?reference=REG123
```

---

## 9. Roles

| Role | Access |
|------|--------|
| `student` | Dashboard, payment history |
| `affiliate` | Affiliate dashboard, register users |
| `admin` | Full admin panel |

Check role from `/api/me` response and navigate accordingly:
```dart
switch (user.role) {
  case 'admin':    navigate to AdminScreen; break;
  case 'affiliate': navigate to AffiliateScreen; break;
  default:         navigate to HomeScreen;
}
```

---

## 10. Polling Payment Status

Recommended polling approach while waiting for payment confirmation:

```dart
Future<void> pollPaymentStatus(String reference) async {
  for (int i = 0; i < 20; i++) {          // max ~2 minutes
    await Future.delayed(Duration(seconds: 6));
    final res = await checkPaymentStatus(reference);
    if (res.status == 'success') {
      saveToken(res.token);
      navigateToHome();
      return;
    }
    if (res.status == 'failed' || res.status == 'cancelled') {
      showError('Payment failed. Please try again.');
      return;
    }
  }
  showError('Payment timed out. Contact support with reference: $reference');
}
```
