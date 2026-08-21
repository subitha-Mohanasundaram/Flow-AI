"""
Workflow Generator
==================
Converts structured user intent into a complete Phase 4–compatible
Universal Workflow JSON document using AI + deterministic templates.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from ai_builder.ai_client import AIClient
from ai_builder.models import BuildResult

# ---------------------------------------------------------------------------
# System prompt for workflow generation
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert workflow automation engineer. Your task is to generate a
complete, production-ready Universal Workflow JSON document from a user's
natural language description.

## Workflow JSON Schema Rules
The output MUST be a valid JSON object with these top-level keys:
  - schema_version: "1.0"
  - workflow_id: snake_case unique ID
  - name: human-readable name
  - description: 1–2 sentence description
  - version: "1.0.0"
  - metadata: { owner, team, labels, created_at, updated_at, category }
  - settings: { max_concurrent_runs, timeout, timezone, execution_mode }
  - variables: array of { name, type, default, description, scope }
  - triggers: array of trigger objects (see below)
  - nodes: array of node objects (see below)

## Node Types
- action: { type: "action", action: { integration, operation, params } }
- condition: { type: "condition", condition_node: { expression, branches } }
- ai: { type: "ai", ai: { provider, model, prompt, output_var } }
- notification: { type: "notification", notification: { targets: [{ channel, to, subject, body }] } }
- webhook: { type: "webhook", webhook: { url, method, headers, body } }
- parallel: { type: "parallel", parallel: { branches: [{ name, nodes }], join_mode } }
- loop: { type: "loop", loop: { mode, collection, body_nodes } }
- transform: { type: "transform", transform: { mappings: [{ target, value }] } }
- human_approval: { type: "human_approval", human_approval: { approvers, title, message } }

## Node Required Fields
Every node needs: id (snake_case), name, type, depends_on (array), description

## Retry / Timeout / Error Handler
Add to nodes where failure is possible:
  retry: { max_attempts: 3, backoff_strategy: "exponential", initial_delay: "PT1S" }
  timeout: { duration: "PT30S" }
  error_handler: { on_error: "continue", fallback_node_id: "..." }

## Triggers
  - cron: { type: "cron", cron: { expression, timezone } }
  - webhook: { type: "webhook", webhook: { path, method, auth } }
  - manual: { type: "manual" }
  - event: { type: "event", event: { source, type, filter } }

## Output Requirements
Return ONLY a raw JSON object. No markdown. No explanation. No comments.
Make the workflow realistic with proper depends_on chains, variable references
using {{variable_name}}, and sensible retry/timeout configs.
"""

_EXPLAIN_SYSTEM = """\
You are a workflow documentation expert. Given a workflow JSON, produce a
clear, friendly explanation for non-technical users.
"""

_INTENT_SYSTEM = """\
You are an intent classifier for a workflow automation platform.
Parse the user's natural language into a structured intent object.
Return JSON only.
"""


class WorkflowGenerator:
    """Generates Universal Workflow JSON from natural language using AI."""

    # Available integrations for context
    _INTEGRATIONS = [
        "google_sheets", "gmail", "google_drive", "google_forms",
        "slack", "github", "openai", "weather", "currency",
        "smtp_email", "rest_api", "jira", "notion", "discord",
        "twilio", "stripe", "postgresql", "mongodb", "redis",
        "aws_s3", "aws_lambda", "azure_blob", "sendgrid",
    ]

    def __init__(self, client: AIClient) -> None:
        self._ai = client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, intent: str, context: Optional[Dict] = None) -> BuildResult:
        """Generate a workflow from a natural language description."""
        try:
            # Step 1: Parse intent
            parsed_intent = self._parse_intent(intent)

            # Step 2: Generate workflow JSON
            workflow_json = self._generate_workflow_json(intent, parsed_intent)

            # Step 3: Post-process and validate
            workflow_json = self._post_process(workflow_json)

            # Step 4: Generate explanation
            explanation = self._generate_explanation(workflow_json)

            nodes       = workflow_json.get("nodes", [])
            plugins_used = list({
                n.get("action", {}).get("integration", "")
                for n in nodes
                if n.get("type") == "action" and n.get("action", {}).get("integration")
            })

            return BuildResult(
                success       = True,
                workflow_json = workflow_json,
                workflow_id   = workflow_json.get("workflow_id"),
                name          = workflow_json.get("name"),
                explanation   = explanation,
                node_count    = len(nodes),
                trigger_type  = workflow_json.get("triggers", [{}])[0].get("type") if workflow_json.get("triggers") else None,
                plugins_used  = [p for p in plugins_used if p],
                raw_intent    = intent,
                confidence    = parsed_intent.get("confidence", 0.85),
            )

        except Exception as exc:
            return BuildResult(
                success = False,
                error   = str(exc),
                raw_intent = intent,
            )

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _parse_intent(self, intent: str) -> Dict[str, Any]:
        """Extract structured intent from natural language."""
        messages = [
            {"role": "system", "content": _INTENT_SYSTEM + """
Parse the user's workflow intent into JSON with these fields:
{
  "trigger_type": "cron|webhook|manual|event|form",
  "trigger_source": "what triggers the workflow (e.g. 'github PR merge')",
  "actions": ["list of high-level actions to perform"],
  "integrations": ["list of services/plugins needed"],
  "conditions": ["list of conditions if any"],
  "data_flow": "brief description of data moving through the workflow",
  "complexity": "simple|moderate|complex",
  "confidence": 0.0-1.0
}"""},
            {"role": "user", "content": f"Parse this workflow intent:\n\n{intent}"},
        ]
        try:
            return self._ai.chat_json(messages, max_tokens=512)
        except Exception:
            return {
                "trigger_type": "manual",
                "actions": ["process", "notify"],
                "integrations": [],
                "complexity": "moderate",
                "confidence": 0.7,
            }

    def _generate_workflow_json(self, intent: str, parsed: Dict) -> Dict:
        """Call AI to generate the full workflow JSON."""
        complexity = parsed.get("complexity", "moderate")
        integrations = parsed.get("integrations", [])

        context_block = f"""
User Intent: {intent}

Parsed Details:
- Trigger type: {parsed.get('trigger_type', 'unknown')}
- Trigger source: {parsed.get('trigger_source', 'unknown')}
- Actions needed: {', '.join(parsed.get('actions', []))}
- Integrations: {', '.join(integrations or ['to be determined'])}
- Conditions: {', '.join(parsed.get('conditions', [])) or 'none'}
- Data flow: {parsed.get('data_flow', 'standard')}
- Complexity: {complexity}

Available integrations: {', '.join(self._INTEGRATIONS)}

Generate a complete workflow JSON for this intent. Make it production-ready
with proper error handling, retries on critical nodes, and sensible timeouts.
For {complexity} workflows, include {{'simple': 3, 'moderate': 6, 'complex': 10}}.get('{complexity}', 6)+ nodes.
"""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": context_block},
        ]
        max_tokens = {"simple": 2000, "moderate": 3500, "complex": 5000}.get(complexity, 3000)
        raw = self._ai.chat(messages, response_format="json", max_tokens=max_tokens)
        return json.loads(raw)

    def _post_process(self, wf: Any) -> Dict:
        """Ensure required fields, fix common AI mistakes."""
        if isinstance(wf, list):
            wf = {"nodes": wf}
        if not isinstance(wf, dict):
            wf = {}
        # Ensure schema_version
        wf.setdefault("schema_version", "1.0")
        wf.setdefault("workflow_id", f"wf_{uuid.uuid4().hex[:8]}")
        wf.setdefault("version", "1.0.0")
        wf.setdefault("variables", [])
        wf.setdefault("nodes", [])
        wf.setdefault("triggers", [{"id": "manual_trigger", "type": "manual"}])

        # Ensure metadata
        wf.setdefault("metadata", {})
        wf["metadata"].setdefault("owner", "ai-builder@platform")
        wf["metadata"].setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        wf["metadata"].setdefault("updated_at", wf["metadata"]["created_at"])

        # Ensure settings
        wf.setdefault("settings", {})
        wf["settings"].setdefault("execution_mode", "dag")
        wf["settings"].setdefault("max_concurrent_runs", 5)
        wf["settings"].setdefault("timeout", "PT10M")

        # Fix node IDs — ensure all are snake_case
        node_ids = set()
        edges = []
        nodes = wf.get("nodes", [])
        has_any_depends = any(node.get("depends_on") for node in nodes[1:]) if len(nodes) > 1 else True
        
        for i, node in enumerate(nodes):
            if not node.get("id"):
                node["id"] = re.sub(r"\W+", "_", node.get("name", "node")).lower()
            node["id"] = re.sub(r"\W+", "_", node["id"]).lower()
            if node["id"] in node_ids:
                node["id"] += f"_{uuid.uuid4().hex[:4]}"
            node_ids.add(node["id"])
            
            if not has_any_depends and i > 0:
                node["depends_on"] = [nodes[i-1]["id"]]
            else:
                node.setdefault("depends_on", [])
                
            node.setdefault("description", node.get("name", ""))
            
            # Auto-layout for React Flow
            if "position" not in node:
                node["position"] = {"x": 250, "y": 100 + (i * 150)}
            
            # Generate edges from depends_on
            for parent in node.get("depends_on", []):
                edges.append({
                    "id": f"e-{parent}-{node['id']}",
                    "source": parent,
                    "target": node['id']
                })
        
        wf["edges"] = edges

        # Ensure first node has no depends_on
        if wf.get("nodes"):
            wf["nodes"][0]["depends_on"] = []

        return wf

    def _generate_explanation(self, workflow_json: Dict) -> str:
        """Generate a plain-English explanation of the workflow."""
        messages = [
            {"role": "system", "content": _EXPLAIN_SYSTEM},
            {"role": "user", "content": f"""Explain this workflow in 2–3 sentences for a non-technical user.
Be friendly and clear. Start with what triggers it, what it does, and what the outcome is.

Workflow: {json.dumps({'name': workflow_json.get('name'), 'description': workflow_json.get('description'), 'nodes': [{'id': n.get('id'), 'name': n.get('name'), 'type': n.get('type')} for n in workflow_json.get('nodes', [])]}, indent=2)}"""},
        ]
        try:
            return self._ai.chat(messages, max_tokens=200).strip()
        except Exception:
            return f"This workflow has {len(workflow_json.get('nodes', []))} steps and processes data automatically."
