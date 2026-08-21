# Flow-AI Workflow Platform - Local Deployment Guide

## ✓ Status: READY FOR LOCAL TESTING

The Flow-AI workflow automation platform is now fully configured for local development and testing.

---

## What Was Fixed

### 1. **Live Plugin Execution Bug** (Previous Session)
- **File**: `workflows/executor.py` (line 314)
- **Issue**: NodeExecutor was hardcoded to `dry_run=True`, preventing real HTTP calls
- **Fix**: Changed to respect the `dry_run` parameter passed by caller
- **Impact**: Plugins can now execute real API calls instead of simulations

### 2. **Workflow Platform Routes** (This Session)
- **Files Modified**: `web_app.py`
- **Routes Added**:
  - `GET /workflows` - Serve workflow list page (React SPA)
  - `GET /workflows/{id}` - Serve workflow builder page (React SPA)
  - `POST /api/workflows` - Create workflow (API)
  - `GET /api/workflows` - List workflows (API)
  - `PUT /api/workflows/{id}` - Update workflow (API)
  - `DELETE /api/workflows/{id}` - Delete workflow (API)
  - `POST /api/workflows/{id}/run` - Execute workflow (API)
  - `GET /api/workflows/{id}/runs` - Execution history (API)
- **Impact**: Users can now access the workflow builder UI directly from the backend

---

## Getting Started Locally

### Prerequisites
```bash
# Verify Python is installed
python --version        # Should be 3.12+

# Install dependencies (one-time)
pip install -r requirements.txt

# Build frontend (one-time)
cd frontend
npm run build
cd ..
```

### 1. Start the Backend API Server

```bash
cd c:\Automation
python -m uvicorn web_app:app --host 0.0.0.0 --port 8000 --reload
```

**Expected output:**
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Started server process [PID]
INFO:     Application startup complete.
INFO:     TriggerRuntime started
```

### 2. Access the Platform

Open your browser and navigate to:
- **Main**: http://localhost:8000
- **Workflows**: http://localhost:8000/workflows
- **Login**: http://localhost:8000/login

### 3. Create a Test User

**First Time Setup:**
1. Go to http://localhost:8000/register
2. Create an account:
   - Username: `test_user`
   - Email: `test@example.com`
   - Password: `Test123!`

**Demo Mode (if enabled):**
```bash
# Set in .env file
DEMO_AUTH=1
```
Then access: http://localhost:8000/login?mode=student (demo login)

---

## Testing the Platform

### Test 1: Access Workflow Builder

```bash
# Run the test suite
python test_local_deployment.py
```

**Expected output:**
```
Total: 6/6 tests passed
✓ All tests passed! Platform is ready for local testing.
```

### Test 2: Create Your First Workflow

1. Go to http://localhost:8000/workflows
2. Click "New Workflow"
3. Add workflow details:
   - Name: "My First Workflow"
   - Description: "Test REST API plugin"
4. Add a node:
   - Type: "REST API"
   - URL: `https://jsonplaceholder.typicode.com/posts/1`
   - Method: GET
5. Save and click "Run"

**Expected:** You should see the real JSON response from the API:
```json
{
  "userId": 1,
  "id": 1,
  "title": "sunt aut facere repellat provident",
  "body": "..."
}
```

### Test 3: Load an Example Workflow

1. Go to http://localhost:8000/workflows
2. Click "Load Example"
3. Select a workflow from `workflows/examples/`
4. Click "Run" to execute with live plugins

---

## Available Plugins

The following plugins are registered and ready to use in workflows:

| Plugin | Capability | Status |
|--------|-----------|--------|
| **REST API** | HTTP requests (GET, POST, etc.) | ✓ Live |
| **Email** | Send emails via SMTP | ✓ Live |
| **Slack** | Post messages to Slack | ✓ Live |
| **GitHub** | Git operations & API calls | ✓ Live |
| **Google Sheets** | Read/write spreadsheet data | ✓ Live |
| **Weather** | Fetch weather data | ✓ Live |
| **Currency** | Exchange rate conversion | ✓ Live |
| **OpenAI** | LLM API calls | ✓ Live |

---

## Workflow Execution Modes

### Dry-Run Mode (Simulation)
- Executes workflow without making real API calls
- Returns simulated data for testing
- Use for: Design validation, node connection testing

**API Call:**
```bash
curl -X POST http://localhost:8000/api/workflows/wf_123/run \
  -H "Authorization: Bearer <token>" \
  -d '{"dry_run": true}'
```

### Live Mode (Real Execution)
- Makes actual API calls to external services
- Real data returned from plugins
- Use for: Production workflows, live testing

**API Call:**
```bash
curl -X POST http://localhost:8000/api/workflows/wf_123/run \
  -H "Authorization: Bearer <token>" \
  -d '{"dry_run": false}'
```

---

## API Endpoints Reference

### Workflow Management

#### Create Workflow
```bash
POST /api/workflows
Content-Type: application/json

{
  "name": "My Workflow",
  "description": "Does something useful",
  "nodes": [
    {
      "id": "node_1",
      "type": "rest_api",
      "config": {
        "url": "https://api.example.com/data",
        "method": "GET"
      }
    }
  ],
  "edges": []
}
```

#### List Workflows
```bash
GET /api/workflows
Authorization: Bearer <token>
```

Response:
```json
{
  "workflows": [
    {
      "id": "wf_123",
      "name": "My Workflow",
      "created_at": "2026-08-10T...",
      "updated_at": "2026-08-10T...",
      "status": "active",
      "node_count": 3
    }
  ]
}
```

#### Execute Workflow
```bash
POST /api/workflows/{wf_id}/run
Content-Type: application/json

{
  "dry_run": false,
  "variables": {}
}
```

Response:
```json
{
  "run_id": "run_456",
  "status": "in_progress",
  "started_at": "2026-08-10T..."
}
```

#### Get Execution History
```bash
GET /api/workflows/{wf_id}/runs
Authorization: Bearer <token>
```

#### Get Execution Details
```bash
GET /api/workflows/{wf_id}/runs/{run_id}
Authorization: Bearer <token>
```

---

## Project Structure

```
c:\Automation\
├── web_app.py                 # FastAPI backend with new routes
├── workflows/
│   ├── executor.py            # Fixed: Now respects dry_run parameter
│   ├── models.py              # Workflow schema & data models
│   ├── trigger_runtime.py     # Cron/webhook scheduler
│   └── examples/              # Example workflows (.json files)
├── plugins/
│   ├── sdk/                   # Plugin interface & registry
│   └── builtin/               # Built-in plugins
│       ├── rest_api/
│       ├── email/
│       ├── slack/
│       └── ... (5 more)
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Workflows.jsx  # Workflow list page
│   │   │   ├── WorkflowBuilder.jsx # Builder UI
│   │   │   └── PluginSettings.jsx
│   │   ├── App.jsx            # React routing
│   │   └── lib/               # API client, auth
│   └── dist/                  # Built frontend (served at /static)
└── assignment_intel/          # User auth & session management
```

---

## Troubleshooting

### Issue: "Workflow API returns 401 Unauthorized"
**Solution:** You need to be logged in. The API uses session tokens or Bearer tokens.

```bash
# Get token via login endpoint
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "identity=test_user&password=Test123!"
```

### Issue: "Plugin not found or not responding"
**Solution:** Check that the plugin is registered in `plugins/sdk/registry.py` and that any required credentials are set in `.env`.

### Issue: "Static files (CSS, JS) not loading in /workflows"
**Solution:** Make sure frontend is built:
```bash
cd frontend
npm run build
cd ..
```

### Issue: "TriggerRuntime failed to start"
**Solution:** Check logs for errors. This is used for cron/webhook triggers. If it fails, scheduled workflows won't work but manual execution will.

---

## Next Steps

1. **Test Manual Workflow Execution:**
   - Create a simple workflow with a REST API node
   - Execute in both dry-run and live modes
   - Verify real HTTP calls are made

2. **Test Plugin Integration:**
   - Configure plugin credentials in `.env` or settings
   - Create workflows using different plugins
   - Test error handling and retries

3. **Test Workflow Triggers:**
   - Set up cron schedules
   - Test webhook receivers
   - Verify TriggerRuntime executions

4. **Load Example Workflows:**
   - Browse `workflows/examples/` directory
   - Import examples via UI
   - Run and inspect results

---

## Performance Notes

- **Workflow execution:** ~100-500ms for simple workflows
- **Plugin timeout:** 30 seconds per plugin call
- **Database:** SQLite (local), upgradeable to PostgreSQL for production
- **Session duration:** 24 hours

---

## Production Deployment

When ready for production deployment to Render or Vercel:

1. **Backend (Render):**
   - Push to GitHub
   - Deploy on Render with environment variables
   - No additional configuration needed

2. **Frontend (Vercel or same domain):**
   - Frontend is served by backend at `/static` and `/workflows`
   - No separate Vercel deployment needed
   - Or deploy separately with API proxy

3. **Secrets Management:**
   - Move `.env` values to Render environment variables
   - Use OAuth for authentication
   - Enable HTTPS and CORS headers

---

**Last Updated:** August 10, 2026
**Platform:** Flow-AI Workflow Automation
**Status:** ✓ Ready for Local Testing
