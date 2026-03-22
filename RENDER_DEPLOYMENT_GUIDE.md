# Render Deployment Guide for English Buddy Payment API

## 🚀 Quick Setup

### Method 1: Automatic Deployment (Recommended)

1. **Connect Repository to Render**
   - Go to https://dashboard.render.com
   - Click "New +" → "Web Service"
   - Connect your GitHub account
   - Select repository: `alphax247-code/english_buddy_server`
   - Click "Connect"

2. **Render will auto-detect settings from `render.yaml`**
   - The configuration is already set up in the repository
   - Review the settings and click "Create Web Service"

3. **Set Environment Variables**

   In the Render dashboard, go to "Environment" and add:

   ```
   SECRET_KEY=bf6a6b359a0f7e2fd4d814843eec86d51c2144a21081f69c6d30c608ea4368bf
   ADMIN_PASSWORD=2018
   PAYSUITE_API_TOKEN=1608|ZnZfOHcAT9vjJZkELCHOoySx8ZANv5ihXxrIFqHj0f8b63c0
   PAYSUITE_WEBHOOK_SECRET=whsec_6f6d2f5c8f42bb4154fe1e4529ded42cccef626ae3fc75ba
   RETURN_URL=https://your-app.onrender.com/payment-return
   CALLBACK_URL=https://your-app.onrender.com/api/paysuite/webhook
   ```

   **IMPORTANT**: Replace `your-app.onrender.com` with your actual Render URL!

4. **Deploy**
   - Click "Manual Deploy" → "Deploy latest commit"
   - Wait for build to complete (2-3 minutes)

---

### Method 2: Manual Configuration

If automatic detection doesn't work:

1. **Build Command**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Start Command**:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port $PORT
   ```

3. **Environment**: `Python 3`

4. **Branch**: `main`

---

## 🔧 Post-Deployment Configuration

### Update PaySuite Webhook URL

After deployment, update your PaySuite webhook URL to:
```
https://your-app.onrender.com/api/paysuite/webhook
```

Replace `your-app.onrender.com` with your actual Render URL.

---

## 🔐 Access Points

Once deployed, your API will be accessible at:

- **Landing Page**: `https://your-app.onrender.com/`
- **Admin Login**: `https://your-app.onrender.com/admin/login`
  - Username: `admin`
  - Password: `2018`
- **Affiliate Login**: `https://your-app.onrender.com/affiliate`
  - Code: Your affiliate code
  - Password: Set by admin

---

## 📊 Monitoring Deployment

### Check Deployment Status

1. Go to your service in Render dashboard
2. Click on "Logs" tab
3. Look for: `Starting FastAPI server on http://0.0.0.0:XXXX`
4. Check for any errors in red

### Common Issues

**Issue**: Build fails with "No module named 'bcrypt'"
- **Solution**: Make sure `bcrypt==4.2.1` is in `requirements.txt`

**Issue**: App crashes on startup
- **Solution**: Check environment variables are set correctly

**Issue**: Port binding error
- **Solution**: Ensure you're using `$PORT` environment variable (already configured)

**Issue**: Database file missing
- **Solution**: The app creates `database.json` automatically on first run

---

## 🔄 Updating Your Deployment

### Automatic Deployment (if enabled)
- Just push to GitHub `main` branch
- Render will automatically rebuild and redeploy

### Manual Deployment
1. Go to Render dashboard
2. Select your service
3. Click "Manual Deploy" → "Deploy latest commit"

---

## 💾 Database Persistence

**IMPORTANT**: Render's free tier uses ephemeral storage. Your `database.json` will be reset on each deploy!

### Solutions:

1. **Recommended**: Upgrade to paid plan with persistent disk
2. **Alternative**: Use external database service (MongoDB Atlas, PostgreSQL on Render)
3. **Temporary**: Accept that database resets on deploy (for testing only)

---

## 🆘 Troubleshooting

### View Application Logs
```
Render Dashboard → Your Service → Logs
```

### Test Endpoints

After deployment, test:

```bash
# Health check
curl https://your-app.onrender.com/

# Admin login
curl -X POST https://your-app.onrender.com/api/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"2018"}'

# Get affiliates (requires admin token)
curl https://your-app.onrender.com/api/admin/affiliates \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

## 📝 Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | JWT token secret |
| `ADMIN_PASSWORD` | Yes | Admin login password (default: 2018) |
| `PAYSUITE_API_TOKEN` | Yes | PaySuite API authentication token |
| `PAYSUITE_WEBHOOK_SECRET` | Yes | PaySuite webhook signature verification |
| `RETURN_URL` | Yes | URL to redirect after payment |
| `CALLBACK_URL` | Optional | PaySuite webhook callback URL |
| `PORT` | Auto-set | Render sets this automatically |

---

## 🎯 Next Steps After Deployment

1. ✅ Verify admin login works
2. ✅ Create first affiliate account
3. ✅ Test payment flow
4. ✅ Update PaySuite webhook URL
5. ✅ Configure proper database solution (if using in production)

---

## 🔗 Useful Links

- **Render Dashboard**: https://dashboard.render.com
- **GitHub Repository**: https://github.com/alphax247-code/english_buddy_server
- **Render Docs**: https://render.com/docs
- **Support**: Check Render community forum or GitHub issues

---

**Need Help?** Check the logs first, then review environment variables.
