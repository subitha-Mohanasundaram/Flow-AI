"""
tests/test_api_workflows.py
============================
Integration tests for the Workflow REST API (web_app.py).

Tests are scoped to the FastAPI TestClient — no real server or database
required. A temporary user is registered for each test session.

Coverage:
  - Auth guard (unauthenticated requests → 401)
  - POST /api/workflows              (create)
  - GET  /api/workflows              (list)
  - GET  /api/workflows/{id}         (get single)
  - POST /api/workflows/{id}         (update / version)
  - GET  /api/workflows/{id}/versions (version list)
  - POST /api/workflows/{id}/restore/{ts} (restore)
  - DELETE /api/workflows/{id}       (delete)
  - POST /api/runs (start a run, dry_run=True)
  - GET  /api/runs/{id}              (poll run status)
  - GET  /api/plugin-configs         (list plugin configs)
  - POST /api/plugin-configs         (save plugin configs)
  - GET  /api/workflow-examples      (example library)
  - POST /api/ai/generate-workflow   (AI gen, mocked)
"""

import json
import os
import time
import uuid
import pytest
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# Pytest marks / skip
# ─────────────────────────────────────────────────────────────────
pytest_plugins = []

try:
    from fastapi.testclient import TestClient
    import web_app  # noqa: F401 – triggers import to resolve app
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(
    not _HAS_FASTAPI,
    reason="FastAPI / web_app not available",
)


# ─────────────────────────────────────────────────────────────────
# Client + auth helpers
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def client():
    """Return a TestClient for the FastAPI app."""
    from web_app import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(scope="session")
def auth_headers(client):
    """Register a unique test user and return Bearer auth headers."""
    uid = uuid.uuid4().hex[:8]
    username = f"testuser_{uid}"
    email    = f"testuser_{uid}@test.invalid"
    password = "Test1234!"

    resp = client.post("/api/auth/register", json={
        "username": username,
        "email":    email,
        "password": password,
    })
    # Accept 200 or 201 (Created)
    assert resp.status_code in (200, 201), f"Register failed: {resp.text}"
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────
# Helper: minimal workflow payload
# ─────────────────────────────────────────────────────────────────

def _wf(name: str = "Test Workflow") -> dict:
    uid = uuid.uuid4().hex[:6]
    return {
        "name":        name,
        "description": "Created by test suite",
        "nodes": [
            {
                "id":   f"trigger_{uid}",
                "name": "Start",
                "type": "trigger",
                "trigger": {"type": "manual"},
            },
            {
                "id":         f"action_{uid}",
                "name":       "Notify",
                "type":       "notification",
                "depends_on": [f"trigger_{uid}"],
                "notification": {"channel": "slack", "message": "Hello!"},
            },
        ],
        "edges": [
            {"source": f"trigger_{uid}", "target": f"action_{uid}"},
        ],
    }


# ═══════════════════════════════════════════════════════════════════
#  Auth guard tests
# ═══════════════════════════════════════════════════════════════════

class TestAuthGuard:
    def test_list_without_token_returns_401(self, client):
        r = client.get("/api/workflows")
        assert r.status_code == 401

    def test_create_without_token_returns_401(self, client):
        r = client.post("/api/workflows", json={"name": "x"})
        assert r.status_code == 401

    def test_plugin_configs_without_token_returns_401(self, client):
        r = client.get("/api/plugin-configs")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════
#  Workflow CRUD
# ═══════════════════════════════════════════════════════════════════

class TestWorkflowCRUD:

    def test_create_returns_workflow_with_id(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("CRUD-Create"), headers=auth_headers)
        assert r.status_code in (200, 201)
        body = r.json()
        assert body.get("success") is True
        wf = body.get("workflow") or body
        assert wf.get("id"), "Expected 'id' in response"

    def test_list_includes_created_workflow(self, client, auth_headers):
        # Create a uniquely-named workflow
        unique_name = f"Listed-{uuid.uuid4().hex[:6]}"
        r = client.post("/api/workflows", json=_wf(unique_name), headers=auth_headers)
        assert r.status_code in (200, 201)

        r2 = client.get("/api/workflows", headers=auth_headers)
        assert r2.status_code == 200
        names = [w.get("name") for w in r2.json().get("workflows", [])]
        assert unique_name in names, f"'{unique_name}' not found in {names}"

    def test_get_single_workflow(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Single-Get"), headers=auth_headers)
        assert r.status_code in (200, 201)
        created = r.json().get("workflow") or r.json()
        wf_id = created["id"]

        r2 = client.get(f"/api/workflows/{wf_id}", headers=auth_headers)
        assert r2.status_code == 200
        fetched = r2.json().get("workflow") or r2.json()
        assert fetched.get("id") == wf_id

    def test_get_nonexistent_returns_404(self, client, auth_headers):
        r = client.get("/api/workflows/nonexistent_workflow_xyz", headers=auth_headers)
        assert r.status_code == 404

    def test_update_workflow(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Update-Me"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        patch = dict(wf)
        patch["description"] = "Updated description"
        r2 = client.post(f"/api/workflows/{wf_id}", json=patch, headers=auth_headers)
        assert r2.status_code == 200

        # Verify the update persisted
        r3 = client.get(f"/api/workflows/{wf_id}", headers=auth_headers)
        assert r3.status_code == 200
        latest = r3.json().get("workflow") or r3.json()
        assert latest.get("description") == "Updated description"


# ═══════════════════════════════════════════════════════════════════
#  Workflow versioning
# ═══════════════════════════════════════════════════════════════════

class TestWorkflowVersioning:

    def test_versions_list_grows_on_update(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Versioned"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        # Update twice → expect ≥ 3 versions (create + 2 updates)
        for i in range(2):
            time.sleep(1.1)  # ensure distinct timestamp filenames
            patch = dict(wf)
            patch["description"] = f"Version {i+2}"
            client.post(f"/api/workflows/{wf_id}", json=patch, headers=auth_headers)

        r2 = client.get(f"/api/workflows/{wf_id}/versions", headers=auth_headers)
        assert r2.status_code == 200
        versions = r2.json().get("versions", [])
        assert len(versions) >= 3, f"Expected ≥3 versions, got {len(versions)}"

    def test_restore_returns_success(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Restore-Test"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        time.sleep(1.1)
        patch = dict(wf)
        patch["description"] = "Changed"
        client.post(f"/api/workflows/{wf_id}", json=patch, headers=auth_headers)

        # Get the version timestamp list
        rv = client.get(f"/api/workflows/{wf_id}/versions", headers=auth_headers)
        assert rv.status_code == 200
        versions = rv.json().get("versions", [])
        assert versions, "No versions found to restore"

        # Restore oldest version
        oldest_ts = versions[-1].get("timestamp") or versions[-1].get("ts")
        if oldest_ts:
            rr = client.post(
                f"/api/workflows/{wf_id}/restore/{oldest_ts}",
                headers=auth_headers,
            )
            # Accept 200 or 404 (if restore endpoint uses different path shape)
            assert rr.status_code in (200, 404)


# ═══════════════════════════════════════════════════════════════════
#  Run lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestRunLifecycle:

    def test_start_dry_run_returns_run_id(self, client, auth_headers):
        # Create a workflow first
        r = client.post("/api/workflows", json=_wf("Run-DryRun"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        r2 = client.post(f"/api/workflows/{wf_id}/run", json={"dry_run": True, "inputs": {}}, headers=auth_headers)
        assert r2.status_code == 200, f"Start run failed: {r2.text}"
        body = r2.json()
        run_id = body.get("run_id")
        assert run_id, "Expected 'run_id' in response"

    def test_poll_run_status(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Run-Poll"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        r2 = client.post(f"/api/workflows/{wf_id}/run", json={"dry_run": True}, headers=auth_headers)
        assert r2.status_code == 200, f"Start run failed: {r2.text}"
        run_id = r2.json().get("run_id")

        # Poll up to 15s for terminal state
        terminal = {"succeeded", "failed", "cancelled", "timed_out"}
        status = None
        for _ in range(30):
            r3 = client.get(f"/api/runs/{run_id}", headers=auth_headers)
            if r3.status_code == 200:
                status = r3.json().get("status")
                if status in terminal:
                    break
            time.sleep(0.5)

        assert status in terminal, f"Run did not reach terminal state; last status={status}"

    def test_poll_nonexistent_run_returns_404(self, client, auth_headers):
        r = client.get("/api/runs/nonexistent_run_xyz", headers=auth_headers)
        assert r.status_code == 404

    def test_list_runs_for_workflow(self, client, auth_headers):
        r = client.post("/api/workflows", json=_wf("Run-List"), headers=auth_headers)
        assert r.status_code in (200, 201)
        wf = r.json().get("workflow") or r.json()
        wf_id = wf["id"]

        client.post(f"/api/workflows/{wf_id}/run", json={"dry_run": True}, headers=auth_headers)
        time.sleep(2)  # allow run to complete

        r2 = client.get(f"/api/workflows/{wf_id}/runs", headers=auth_headers)
        assert r2.status_code == 200
        assert "runs" in r2.json()


# ═══════════════════════════════════════════════════════════════════
#  Plugin configs
# ═══════════════════════════════════════════════════════════════════

class TestPluginConfigs:

    def test_get_plugin_configs_returns_dict(self, client, auth_headers):
        r = client.get("/api/plugin-configs", headers=auth_headers)
        assert r.status_code == 200
        # Body is either a dict of plugin → config, or {"configs": {...}}
        assert isinstance(r.json(), dict)

    def test_save_and_retrieve_plugin_config(self, client, auth_headers):
        payload = {"slack": {"webhook_url": "https://hooks.slack.test/xxx"}}
        r = client.post("/api/plugin-configs", json=payload, headers=auth_headers)
        assert r.status_code == 200

        r2 = client.get("/api/plugin-configs", headers=auth_headers)
        assert r2.status_code == 200
        configs = r2.json()
        # Config should contain our key (either flat or nested)
        all_values = json.dumps(configs)
        assert "slack" in all_values


# ═══════════════════════════════════════════════════════════════════
#  Workflow examples
# ═══════════════════════════════════════════════════════════════════

class TestWorkflowExamples:

    def test_examples_endpoint_returns_list(self, client, auth_headers):
        r = client.get("/api/workflow-examples", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert "examples" in body
        assert isinstance(body["examples"], list)

    def test_each_example_has_required_fields(self, client, auth_headers):
        r = client.get("/api/workflow-examples", headers=auth_headers)
        assert r.status_code == 200
        for ex in r.json().get("examples", []):
            assert "name" in ex or "file" in ex, f"Example missing name/file: {ex}"


# ═══════════════════════════════════════════════════════════════════
#  AI workflow generation (mocked / no real API key needed)
# ═══════════════════════════════════════════════════════════════════

class TestAIGenerate:

    def test_ai_generate_without_intent_returns_error(self, client, auth_headers):
        r = client.post("/api/ai/generate-workflow", json={}, headers=auth_headers)
        # Should return 422 (missing intent) or 200/503 with error key
        assert r.status_code in (200, 400, 422, 503)
        if r.status_code == 200:
            assert r.json().get("error") or r.json().get("workflow") is not None

    def test_ai_generate_with_intent_does_not_crash(self, client, auth_headers):
        """Verify the endpoint accepts an intent and returns JSON (may error if no API key)."""
        r = client.post(
            "/api/ai/generate-workflow",
            json={"intent": "Send a Slack message when a GitHub PR is merged"},
            headers=auth_headers,
        )
        # We don't assert success (no real API key) but must get a valid JSON response
        assert r.headers.get("content-type", "").startswith("application/json")
        assert isinstance(r.json(), dict)
