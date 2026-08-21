import os
import sys
import json
import time
import uuid
import requests
from pathlib import Path

# Load .env manually to ensure JWT_SECRET matches the server's key
def load_env():
    p = Path(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()

load_env()

from assignment_intel.auth import issue_session_token

BASE_URL = "http://localhost:8000"

# 1. Issue Token for Authenticated Requests using the correct secret from env
admin_token = issue_session_token(user_id=14, username='subi', role='admin')
headers = {
    "Authorization": f"Bearer {admin_token}",
    "Content-Type": "application/json"
}

print(f"Issued Admin JWT Token: {admin_token[:40]}...")

results = {}
verification_logs = []

def log_test(step_name, passed, message):
    results[step_name] = "PASS" if passed else "FAIL"
    verification_logs.append(f"[{'PASS' if passed else 'FAIL'}] {step_name}: {message}")
    print(f"[{'PASS' if passed else 'FAIL'}] {step_name}: {message}")

# --- Test 1: AI Chatbot API ---
try:
    chat_payload = {
        "messages": [
            {"role": "user", "content": "Get the current weather for Chennai and send the summary to my email."}
        ]
    }
    r = requests.post(f"{BASE_URL}/api/ai/chat", json=chat_payload, headers=headers)
    if r.status_code == 200:
        res_data = r.json()
        log_test("AI Chatbot API", "type" in res_data, f"Response: {res_data}")
    else:
        log_test("AI Chatbot API", False, f"HTTP Error {r.status_code}: {r.text}")
except Exception as e:
    log_test("AI Chatbot API", False, f"Exception: {e}")

# --- Test 2: Workflow Generation & Validation ---
try:
    from ai_builder.ai_client import AIClient
    from ai_builder.generator import WorkflowGenerator
    
    client = AIClient()
    gen = WorkflowGenerator(client)
    intent = "Get the current weather for Chennai using API key 12345fakekey, format the response in a transform node, and email it to subi.re17@gmail.com. First node node_1, second node_2, third node_3. Make sure to set proper depends_on."
    build_res = gen.generate(intent)
    
    if build_res.success and build_res.workflow_json:
        wf = build_res.workflow_json
        has_nodes = len(wf.get("nodes", [])) > 0
        has_edges = len(wf.get("edges", [])) > 0 or len(wf.get("nodes", [])) == 1
        log_test("Workflow Generation & Schema", has_nodes and has_edges, 
                 f"Name: {wf.get('name')}, Nodes: {len(wf.get('nodes', []))}, Edges: {len(wf.get('edges', []))}")
    else:
        log_test("Workflow Generation & Schema", False, f"Generation failed: {build_res.explanation}")
except Exception as e:
    log_test("Workflow Generation & Schema", False, f"Exception: {e}")

# --- Test 3: Create, Version, and Rollback Workflow ---
try:
    wf_payload = {
        "name": "E2E Master Verification Workflow",
        "description": "Used to test all capabilities of Flow AI",
        "nodes": [
            {
                "id": "node_1",
                "name": "Manual Start",
                "type": "transform",
                "depends_on": [],
                "transform": {
                    "mappings": [
                        {"target": "city", "value": "Chennai"}
                    ]
                }
            }
        ]
    }
    
    # 1. Create
    r_create = requests.post(f"{BASE_URL}/api/workflows", json=wf_payload, headers=headers)
    if r_create.status_code == 201:
        created_wf = r_create.json()
        wf_id = created_wf["id"]
        
        # 2. Update to create version snapshot 1
        wf_payload["name"] = "E2E Master Verification Workflow V1"
        requests.put(f"{BASE_URL}/api/workflows/{wf_id}", json=wf_payload, headers=headers)
        
        # 3. Update to create version snapshot 2
        wf_payload["name"] = "E2E Master Verification Workflow V2"
        wf_payload["nodes"].append({
            "id": "node_2",
            "name": "Add Info",
            "type": "transform",
            "depends_on": ["node_1"],
            "transform": {
                "mappings": [{"target": "status", "value": "verified"}]
            }
        })
        requests.put(f"{BASE_URL}/api/workflows/{wf_id}", json=wf_payload, headers=headers)
        
        # 4. Get Versions
        r_vers = requests.get(f"{BASE_URL}/api/workflows/{wf_id}/versions", headers=headers)
        versions = r_vers.json()["versions"] if r_vers.status_code == 200 else []
        
        # 5. Rollback to V1
        if len(versions) >= 2:
            # Rollback to the first version (which should have node_count = 1)
            # Versions list is sorted newest first or oldest first? Let's assume list order.
            # Usually sorted newest first, so oldest (V1) is at versions[-1]
            ver_ts = versions[-1]["version_ts"]
            r_rollback = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/versions/{ver_ts}/restore", headers=headers)
            rolled_wf = requests.get(f"{BASE_URL}/api/workflows/{wf_id}", headers=headers).json()
            
            passed = len(rolled_wf.get("nodes", [])) == 1
            log_test("Workflow Versioning & Rollback", passed, f"Rollback restored workflow node count: {len(rolled_wf.get('nodes', []))} (expected 1)")
        else:
            log_test("Workflow Versioning & Rollback", False, f"Could not fetch 2 versions: {versions}")
    else:
        log_test("Workflow Versioning & Rollback", False, f"Failed to create workflow: {r_create.text}")
except Exception as e:
    log_test("Workflow Versioning & Rollback", False, f"Exception: {e}")

# --- Test 4: Live Execution with Huey Workers ---
try:
    exec_wf_payload = {
        "name": "Huey E2E Execution Test",
        "nodes": [
            {
                "id": "transform_1",
                "name": "Init Data",
                "type": "transform",
                "depends_on": [],
                "transform": {
                    "mappings": [
                        {"target": "greeting", "value": "Hello from Huey"}
                    ]
                }
            }
        ],
        "edges": []
    }
    
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=exec_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        run_resp = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/run", headers=headers)
        if run_resp.status_code in (200, 201, 202):
            run_data = run_resp.json()
            run_id = run_data["run_id"]
            
            completed = False
            run_rec = None
            for _ in range(15):
                time.sleep(1)
                run_dir = Path("workflows/runs") / wf_id
                run_file = run_dir / f"{run_id}.json"
                if run_file.exists():
                    try:
                        run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                        if run_rec.get("status") in ("succeeded", "failed"):
                            completed = True
                            break
                    except Exception:
                        pass
                        
            if completed:
                log_test("Huey worker execution & manual trigger", run_rec.get("status") == "succeeded", 
                         f"Async run complete. Status: {run_rec.get('status')}")
            else:
                log_test("Huey worker execution & manual trigger", False, f"Timeout or run record missing. Last rec: {run_rec}")
        else:
            log_test("Huey worker execution & manual trigger", False, f"Run trigger failed: {run_resp.text}")
    else:
        log_test("Huey worker execution & manual trigger", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Huey worker execution & manual trigger", False, f"Exception: {e}")

# --- Test 5: Human-in-the-Loop & Approval node ---
try:
    approval_wf_payload = {
        "name": "Human Approval E2E Test",
        "nodes": [
            {
                "id": "gate_1",
                "name": "Manager Approval",
                "type": "human_approval",
                "depends_on": [],
                "human_approval": {
                    "approvers": ["admin"],
                    "title": "Verification Approval Gate",
                    "message": "Click approve to proceed",
                    "timeout_seconds": 10
                }
            },
            {
                "id": "action_post_approval",
                "name": "Post Approval Logger",
                "type": "transform",
                "depends_on": ["gate_1"],
                "transform": {
                    "mappings": [{"target": "msg", "value": "Approved successfully"}]
                }
            }
        ]
    }
    
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=approval_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        run_resp = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/run", headers=headers)
        if run_resp.status_code in (200, 201, 202):
            run_id = run_resp.json()["run_id"]
            
            # Wait for workflow to reach approval node
            time.sleep(2)
            
            run_file = Path("workflows/runs") / wf_id / f"{run_id}.json"
            
            # Signal approval
            appr_resp = requests.post(f"{BASE_URL}/api/runs/{run_id}/approve", json={"comment": "Verified E2E"}, headers=headers)
            
            # Wait for worker to finish
            completed = False
            run_rec = None
            for _ in range(10):
                time.sleep(1)
                if run_file.exists():
                    try:
                        run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                        if run_rec.get("status") in ("succeeded", "failed"):
                            completed = True
                            break
                    except Exception:
                        pass
                        
            passed = completed and run_rec.get("status") == "succeeded"
            log_test("Human approval & workflow resume", passed, 
                     f"Approve Response: {appr_resp.status_code}, Final Run Status: {run_rec.get('status') if run_rec else 'None'}")
        else:
            log_test("Human approval & workflow resume", False, f"Trigger failed: {run_resp.text}")
    else:
        log_test("Human approval & workflow resume", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Human approval & workflow resume", False, f"Exception: {e}")

# --- Test 6: Dynamic Personalization AI Node ---
try:
    ai_node_wf_payload = {
        "name": "AI Personalization E2E Test",
        "nodes": [
            {
                "id": "ai_personalizer",
                "name": "Generate Welcome Message",
                "type": "ai",
                "depends_on": [],
                "ai": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "prompt": "Create a short 5-word welcome message for user Arun whose preference is concise.",
                    "output_var": "welcome_message"
                }
            }
        ]
    }
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=ai_node_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        run_resp = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/run", headers=headers)
        if run_resp.status_code in (200, 201, 202):
            run_id = run_resp.json()["run_id"]
            run_file = Path("workflows/runs") / wf_id / f"{run_id}.json"
            
            completed = False
            run_rec = None
            for _ in range(10):
                time.sleep(1)
                if run_file.exists():
                    try:
                        run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                        if run_rec.get("status") in ("succeeded", "failed"):
                            completed = True
                            break
                    except Exception:
                        pass
            
            node_states = run_rec.get("node_states", {}) if run_rec else {}
            ai_state = node_states.get("ai_personalizer", {})
            outputs = ai_state.get("outputs", {})
            passed = completed and ai_state.get("status") == "success"
            log_test("Personalization AI Node", passed, f"AI Status: {ai_state.get('status')}, Outputs: {outputs}")
        else:
            log_test("Personalization AI Node", False, f"Trigger failed: {run_resp.text}")
    else:
        log_test("Personalization AI Node", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Personalization AI Node", False, f"Exception: {e}")

# --- Test 7: Loop Node Execution ---
try:
    loop_wf_payload = {
        "name": "Loop Iteration E2E Test",
        "variables": [
            {"name": "items", "type": "array", "default": ["A", "B", "C"]}
        ],
        "nodes": [
            {
                "id": "loop_1",
                "name": "Loop over items",
                "type": "loop",
                "depends_on": [],
                "loop": {
                    "mode": "for_each",
                    "collection": "${items}",
                    "body_nodes": [
                        {
                            "id": "loop_sub_action",
                            "name": "Transform Item",
                            "type": "transform",
                            "depends_on": [],
                            "transform": {
                                "mappings": [
                                    {"target": "processed", "value": "Item {{$item}} processed"}
                                ]
                            }
                        }
                    ]
                }
            }
        ]
    }
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=loop_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        run_resp = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/run", headers=headers)
        if run_resp.status_code in (200, 201, 202):
            run_id = run_resp.json()["run_id"]
            run_file = Path("workflows/runs") / wf_id / f"{run_id}.json"
            
            completed = False
            run_rec = None
            for _ in range(10):
                time.sleep(1)
                if run_file.exists():
                    try:
                        run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                        if run_rec.get("status") in ("succeeded", "failed"):
                            completed = True
                            break
                    except Exception:
                        pass
            
            node_states = run_rec.get("node_states", {}) if run_rec else {}
            loop_state = node_states.get("loop_1", {})
            passed = completed and loop_state.get("status") == "success"
            log_test("Loop Node Execution", passed, f"Loop Node Status: {loop_state.get('status')}, Outputs: {loop_state.get('outputs')}")
        else:
            log_test("Loop Node Execution", False, f"Trigger failed: {run_resp.text}")
    else:
        log_test("Loop Node Execution", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Loop Node Execution", False, f"Exception: {e}")

# --- Test 8: Omnichannel Fallback ---
try:
    fallback_wf_payload = {
        "name": "Fallback Action E2E Test",
        "nodes": [
            {
                "id": "webhook_failing",
                "name": "Primary Webhook Call",
                "type": "webhook",
                "depends_on": [],
                "webhook": {
                    "url": "http://localhost:8000/api/nonexistent-endpoint-to-force-failure",
                    "method": "POST"
                },
                "error_handler": {
                    "on_error": "continue",
                    "fallback_node_id": "notification_fallback"
                }
            },
            {
                "id": "notification_fallback",
                "name": "Fallback Notification",
                "type": "transform",
                "depends_on": ["webhook_failing"],
                "transform": {
                    "mappings": [
                        {"target": "alert", "value": "Fallback triggered successfully"}
                    ]
                }
            }
        ]
    }
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=fallback_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        run_resp = requests.post(f"{BASE_URL}/api/workflows/{wf_id}/run", headers=headers)
        if run_resp.status_code in (200, 201, 202):
            run_id = run_resp.json()["run_id"]
            run_file = Path("workflows/runs") / wf_id / f"{run_id}.json"
            
            completed = False
            run_rec = None
            for _ in range(10):
                time.sleep(1)
                if run_file.exists():
                    try:
                        run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                        if run_rec.get("status") in ("succeeded", "failed"):
                            completed = True
                            break
                    except Exception:
                        pass
            
            node_states = run_rec.get("node_states", {}) if run_rec else {}
            primary_state = node_states.get("webhook_failing", {})
            fallback_state = node_states.get("notification_fallback", {})
            passed = completed and primary_state.get("status") == "failed" and fallback_state.get("status") == "success"
            log_test("Omnichannel Fallback", passed, f"Primary Status: {primary_state.get('status')}, Fallback Status: {fallback_state.get('status')}")
        else:
            log_test("Omnichannel Fallback", False, f"Trigger failed: {run_resp.text}")
    else:
        log_test("Omnichannel Fallback", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Omnichannel Fallback", False, f"Exception: {e}")

# --- Test 9: Webhook Trigger ---
try:
    webhook_uuid = uuid.uuid4().hex[:8]
    wh_wf_payload = {
        "name": "Webhook Trigger E2E Test",
        "triggers": [
            {
                "id": "wh_incoming",
                "type": "webhook",
                "webhook_path": f"/webhooks/custom_path_{webhook_uuid}"
            }
        ],
        "nodes": [
            {
                "id": "node_logger",
                "name": "Log Payload",
                "type": "transform",
                "depends_on": [],
                "transform": {
                    "mappings": [
                        {"target": "received_name", "value": "{{_trigger.name}}"}
                    ]
                }
            }
        ]
    }
    create_resp = requests.post(f"{BASE_URL}/api/workflows", json=wh_wf_payload, headers=headers)
    if create_resp.status_code == 201:
        wf_id = create_resp.json()["id"]
        
        path = wh_wf_payload["triggers"][0]["webhook_path"]
        
        # Reload trigger runtime by updating the workflow
        requests.put(f"{BASE_URL}/api/workflows/{wf_id}", json=wh_wf_payload, headers=headers)
        
        # Give trigger runtime 1 second to update
        time.sleep(1)
        
        webhook_payload = {
            "name": "Subitha",
            "test": True,
            "source": "Flow AI"
        }
        wh_resp = requests.post(f"{BASE_URL}/api{path}", json=webhook_payload)
        
        runs_resp = requests.get(f"{BASE_URL}/api/workflows/{wf_id}/runs", headers=headers)
        runs_list = runs_resp.json() if runs_resp.status_code == 200 else []
        
        passed = len(runs_list) > 0 and wh_resp.status_code == 202
        log_test("Webhook Trigger", passed, f"Webhook status: {wh_resp.status_code}, Runs triggered: {len(runs_list)}")
    else:
        log_test("Webhook Trigger", False, f"Creation failed: {create_resp.text}")
except Exception as e:
    log_test("Webhook Trigger", False, f"Exception: {e}")

# --- Test 10: ChromaDB RAG Retrieval Verification ---
try:
    from ai_builder.rag import search_context
    docs = search_context("concise email voice")
    passed = len(docs) > 0
    log_test("ChromaDB RAG Retrieval", passed, f"Retrieved documents: {docs}")
except Exception as e:
    log_test("ChromaDB RAG Retrieval", False, f"Exception: {e}")

# --- Test 11: SSE Auth Validation ---
try:
    r_sse_no_auth = requests.get(f"{BASE_URL}/api/runs/some_run_id/stream")
    r_sse_auth = requests.get(f"{BASE_URL}/api/runs/some_run_id/stream?token={admin_token}", stream=True)
    
    passed = r_sse_no_auth.status_code == 401 and r_sse_auth.status_code == 200
    log_test("SSE Authentication", passed, f"No Auth HTTP Status: {r_sse_no_auth.status_code}, Auth HTTP Status: {r_sse_auth.status_code}")
except Exception as e:
    log_test("SSE Authentication", False, f"Exception: {e}")

# --- Test 12: Subworkflow Execution ---
try:
    sub_wf_payload = {
        "name": "Child Subworkflow",
        "nodes": [
            {
                "id": "child_log",
                "name": "Child Work",
                "type": "transform",
                "depends_on": [],
                "transform": {
                    "mappings": [{"target": "child_status", "value": "done"}]
                }
            }
        ]
    }
    sub_create_resp = requests.post(f"{BASE_URL}/api/workflows", json=sub_wf_payload, headers=headers)
    if sub_create_resp.status_code == 201:
        sub_wf_id = sub_create_resp.json()["id"]
        
        # Save subworkflow child JSON with the correct ID filename in the database saved path
        # wait! _save_wf saves it as sub_wf_id.json
        # The executor searches for Path("workflows") / "saved" / f"{sub_id}.json"
        # Since requests.post saves it correctly, this file will exist!
        
        parent_wf_payload = {
            "name": "Parent Subworkflow Test",
            "nodes": [
                {
                    "id": "sub_call_1",
                    "name": "Execute Subworkflow",
                    "type": "subworkflow",
                    "depends_on": [],
                    "subworkflow": {
                        "workflow_id": sub_wf_id
                    }
                }
            ]
        }
        parent_create_resp = requests.post(f"{BASE_URL}/api/workflows", json=parent_wf_payload, headers=headers)
        if parent_create_resp.status_code == 201:
            parent_wf_id = parent_create_resp.json()["id"]
            run_resp = requests.post(f"{BASE_URL}/api/workflows/{parent_wf_id}/run", headers=headers)
            if run_resp.status_code in (200, 201, 202):
                run_id = run_resp.json()["run_id"]
                run_file = Path("workflows/runs") / parent_wf_id / f"{run_id}.json"
                
                completed = False
                run_rec = None
                for _ in range(10):
                    time.sleep(1)
                    if run_file.exists():
                        try:
                            run_rec = json.loads(run_file.read_text(encoding="utf-8"))
                            if run_rec.get("status") in ("succeeded", "failed"):
                                completed = True
                                break
                        except Exception:
                            pass
                            
                passed = completed and run_rec.get("status") == "succeeded"
                log_test("Subworkflow Execution", passed, f"Parent Run Status: {run_rec.get('status') if run_rec else 'None'}")
            else:
                log_test("Subworkflow Execution", False, f"Trigger failed: {run_resp.text}")
        else:
            log_test("Subworkflow Execution", False, f"Parent Creation failed: {parent_create_resp.text}")
    else:
        log_test("Subworkflow Execution", False, f"Child Creation failed: {sub_create_resp.text}")
except Exception as e:
    log_test("Subworkflow Execution", False, f"Exception: {e}")

# --- Summary Output ---
print("\n=== FINAL TEST CHECKLIST RESULT ===")
for k, v in results.items():
    print(f"{k}: {v}")
