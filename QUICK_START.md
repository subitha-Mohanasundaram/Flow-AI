# Flow-AI Quick Start (5 Minutes)

## Start the Server

```bash
cd c:\Automation
python -m uvicorn web_app:app --host 0.0.0.0 --port 8000 --reload
```

Wait for:
```
Uvicorn running on http://0.0.0.0:8000
Application startup complete.
TriggerRuntime started
```

## Open the App

1. Browser: **http://localhost:8000/workflows**
2. Login with your credentials (or register new account)
3. Click "New Workflow"

## Create & Run a Workflow

### Example: REST API Call
1. Name: "My First Workflow"
2. Add Node → REST API
3. URL: `https://jsonplaceholder.typicode.com/posts/1`
4. Method: GET
5. Click "Save"
6. Click "Run"

**Expected:** See real JSON response from the API

```json
{
  "userId": 1,
  "id": 1,
  "title": "sunt aut facere repellat provident...",
  "body": "..."
}
```

## Verify Everything Works

```bash
# In a new terminal
python test_local_deployment.py
```

Expected: `✓ 6/6 tests passed`

## What's Running

- **Backend API**: http://localhost:8000
- **Workflows Page**: http://localhost:8000/workflows
- **API Docs**: http://localhost:8000/docs (if available)

## Available Plugins

- REST API ✓
- Email ✓
- Slack ✓
- GitHub ✓
- Google Sheets ✓
- Weather ✓
- Currency ✓
- OpenAI ✓

## Next: Try Example Workflows

1. Go to http://localhost:8000/workflows
2. Click "Load Example"
3. Select a workflow
4. Click "Run"

## Documentation

- **Full Setup**: Read `LOCAL_DEPLOYMENT_GUIDE.md`
- **Bug Fix Details**: Read `BUGFIX_SUMMARY.md`
- **Troubleshooting**: Read `LOCAL_DEPLOYMENT_GUIDE.md` → Troubleshooting section

## Common Tasks

### Create Custom Workflow
1. Click "New Workflow"
2. Add nodes (REST API, Email, Transform, etc.)
3. Connect nodes with arrows
4. Configure each node
5. Click "Save" then "Run"

### Run in Dry-Run Mode (Simulation)
1. Before clicking "Run", uncheck "Live Mode"
2. Click "Run"
3. See simulated outputs (no real API calls)

### Run in Live Mode (Real Execution)
1. Check "Live Mode" checkbox
2. Click "Run"
3. See real API responses

### Check Execution History
1. Go to workflow
2. Click "History"
3. Click on a run to see details

## API Examples

```bash
# List workflows
curl http://localhost:8000/api/workflows

# Create workflow
curl -X POST http://localhost:8000/api/workflows \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test",
    "description": "My workflow",
    "nodes": [],
    "edges": []
  }'

# Execute workflow
curl -X POST http://localhost:8000/api/workflows/WF_ID/run \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}'
```

## Stop the Server

Press `Ctrl+C` in the terminal running Uvicorn.

---

**That's it! Start with the server and open http://localhost:8000/workflows**

For detailed documentation, see:
- `LOCAL_DEPLOYMENT_GUIDE.md` - Complete setup guide
- `BUGFIX_SUMMARY.md` - Technical details on the fix
- `DEPLOYMENT_COMPLETE.md` - Full summary
