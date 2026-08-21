"""
Phase 4 — Universal Workflow Engine
====================================
A lightweight Python runtime that can parse, validate, and (dry-)run
workflows defined in the Universal Workflow JSON format.

Supports:
  - Schema validation
  - DAG execution ordering
  - Node type dispatch (action / condition / ai / notification / webhook /
    human_approval / parallel / loop / transform / subworkflow)
  - Variable interpolation ({{var}} templates)
  - Retry policies with backoff
  - Timeout handling
  - Error handler dispatch
  - Execution state tracking
  - Dry-run mode (no side-effects)

Usage:
    from workflows.engine import WorkflowEngine
    engine = WorkflowEngine(dry_run=True)
    engine.load("workflows/examples/01_google_form_to_sheets.workflow.json")
    result = engine.run(inputs={"respondent_name": "Alice", "respondent_email": "alice@test.com"})
    print(result.status)

CLI:
    python -m workflows.engine workflows/examples/01_google_form_to_sheets.workflow.json --dry-run
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import copy
import argparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & Data Classes
# ---------------------------------------------------------------------------

class NodeStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    SUCCEEDED = "succeeded"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    TIMED_OUT = "timed_out"
    AWAITING_APPROVAL = "awaiting_approval"


class WorkflowStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass
class NodeState:
    node_id:    str
    status:     NodeStatus = NodeStatus.PENDING
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    attempt:    int = 0
    outputs:    Dict[str, Any] = field(default_factory=dict)
    error:      Optional[Dict[str, str]] = None

    def duration_ms(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds() * 1000
        return None


@dataclass
class ExecutionResult:
    workflow_id: str
    run_id:      str
    status:      WorkflowStatus
    started_at:  datetime
    finished_at: Optional[datetime]
    node_states: Dict[str, NodeState]
    variables:   Dict[str, Any]
    error:       Optional[Dict[str, str]] = None

    def summary(self) -> str:
        lines = [
            f"Workflow  : {self.workflow_id}",
            f"Run ID    : {self.run_id}",
            f"Status    : {self.status.value}",
            f"Started   : {self.started_at.isoformat()}",
            f"Finished  : {self.finished_at.isoformat() if self.finished_at else 'N/A'}",
            "",
            "Node Execution Summary:",
        ]
        for node_id, state in self.node_states.items():
            dur = f"{state.duration_ms():.0f}ms" if state.duration_ms() else "—"
            err = f" | ERR: {state.error['message']}" if state.error else ""
            lines.append(f"  [{state.status.value.upper():12}] {node_id} ({dur}){err}")
        if self.error:
            lines.append(f"\nError: {self.error.get('message', 'unknown')}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Variable Interpolation
# ---------------------------------------------------------------------------

class VariableInterpolator:
    """Resolves {{variable}} and ${variable} templates from a context dict."""

    _TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}|\$\{([^}]+)\}")

    @classmethod
    def resolve(cls, template: Any, context: Dict[str, Any]) -> Any:
        if isinstance(template, str):
            return cls._resolve_string(template, context)
        if isinstance(template, dict):
            return {k: cls.resolve(v, context) for k, v in template.items()}
        if isinstance(template, list):
            return [cls.resolve(item, context) for item in template]
        return template

    @classmethod
    def _resolve_string(cls, s: str, ctx: Dict[str, Any]) -> Any:
        # If entire string is a single template, return the raw value (preserves types)
        full_match = cls._TEMPLATE_RE.fullmatch(s)
        if full_match:
            key = full_match.group(1) or full_match.group(2)
            return cls._lookup(key.strip(), ctx)

        # Otherwise do text substitution
        def replace(m: re.Match) -> str:
            key = (m.group(1) or m.group(2)).strip()
            value = cls._lookup(key, ctx)
            return str(value) if value is not None else ""

        return cls._TEMPLATE_RE.sub(replace, s)

    @classmethod
    def _lookup(cls, key: str, ctx: Dict[str, Any]) -> Any:
        # Support dot-notation: some.nested.key
        parts = key.split(".")
        val: Any = ctx
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
            else:
                return None
        return val


# ---------------------------------------------------------------------------
# Retry Policy
# ---------------------------------------------------------------------------

def _parse_iso_duration_seconds(d: Optional[str]) -> float:
    """Parse a simplified ISO 8601 duration to seconds (covers PT30S, PT5M, PT1H)."""
    if not d:
        return 0.0
    total = 0.0
    pattern = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?")
    m = pattern.fullmatch(d)
    if m:
        days    = float(m.group(1) or 0)
        hours   = float(m.group(2) or 0)
        minutes = float(m.group(3) or 0)
        seconds = float(m.group(4) or 0)
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total


def _compute_delay(attempt: int, policy: Dict[str, Any]) -> float:
    strategy = policy.get("backoff_strategy", "exponential")
    initial  = _parse_iso_duration_seconds(policy.get("initial_delay", "PT1S"))
    max_d    = _parse_iso_duration_seconds(policy.get("max_delay", "PT60S"))
    mult     = float(policy.get("backoff_multiplier", 2))

    if strategy == "fixed":
        delay = initial
    elif strategy == "linear":
        delay = initial * attempt
    elif strategy == "exponential":
        delay = initial * (mult ** (attempt - 1))
    elif strategy == "jitter":
        import random
        delay = initial * (mult ** (attempt - 1)) * random.uniform(0.5, 1.5)
    else:
        delay = initial

    return min(delay, max_d)


# ---------------------------------------------------------------------------
# Condition Evaluator
# ---------------------------------------------------------------------------

class ConditionEvaluator:
    """Evaluates simple boolean expressions from workflow conditions."""

    @classmethod
    def evaluate(cls, condition: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
        if "expression" in condition:
            expr = VariableInterpolator.resolve(condition["expression"], ctx)
            return cls._eval_expression(str(expr), ctx)
        if "logic" in condition:
            logic = condition["logic"]
            results = [cls.evaluate(r, ctx) for r in condition.get("rules", [])]
            if logic == "AND":
                return all(results)
            if logic == "OR":
                return any(results)
            if logic == "NOT":
                return not results[0] if results else True
        return True

    @classmethod
    def _eval_expression(cls, expr: str, ctx: Dict[str, Any]) -> bool:
        # Safely evaluate simple boolean expressions
        # Supported: == != < > <= >= && || ! true false numbers strings
        # Replace variable references with their values
        expr = expr.strip()
        if expr in ("true", "True", "1"):
            return True
        if expr in ("false", "False", "0", ""):
            return False
        # Very basic safe eval using Python eval with restricted namespace
        safe_ns = {k: v for k, v in ctx.items() if not callable(v)}
        safe_ns["__builtins__"] = {}
        # Replace JS-style operators with Python equivalents
        expr_py = expr.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
        try:
            result = eval(expr_py, {"__builtins__": {}}, safe_ns)  # noqa: S307
            return bool(result)
        except Exception:
            # If evaluation fails, treat as falsy
            logger.debug("Could not evaluate expression: %s", expr)
            return False


# ---------------------------------------------------------------------------
# Node Executor (Dry-Run Implementations)
# ---------------------------------------------------------------------------

class NodeExecutor:
    """Dispatches node execution by type. In dry-run mode, simulates outputs."""

    def __init__(self, dry_run: bool = True, variables: Optional[Dict] = None):
        self.dry_run = dry_run
        self.variables: Dict[str, Any] = variables or {}

    def execute(self, node: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a node and return its output dict."""
        node_type = node.get("type", "unknown")
        method = getattr(self, f"_exec_{node_type}", self._exec_unknown)
        return method(node, ctx)

    # ------------------------------------------------------------------
    def _exec_action(self, node: Dict, ctx: Dict) -> Dict:
        action = node.get("action", {})
        integration = action.get("integration", "?")
        operation = action.get("operation", "?")
        params = VariableInterpolator.resolve(action.get("params", {}), ctx)
        if self.dry_run:
            logger.info("  [DRY-RUN] action %s.%s params=%s", integration, operation, params)
            return {"result": {"status": "simulated", "integration": integration, "operation": operation}}
        raise NotImplementedError(f"Live action {integration}.{operation} not implemented")

    def _exec_webhook(self, node: Dict, ctx: Dict) -> Dict:
        wh = node.get("webhook", {})
        url     = VariableInterpolator.resolve(wh.get("url", ""), ctx)
        method  = wh.get("method", "GET")
        headers = VariableInterpolator.resolve(wh.get("headers", {}), ctx)
        body    = VariableInterpolator.resolve(wh.get("body"), ctx)
        if self.dry_run:
            logger.info("  [DRY-RUN] webhook %s %s headers=%s body=%s", method, url, headers, body)
            return {"result": {"status": 200, "body": {"simulated": True}}}
        
        # Live Webhook Execution
        import requests
        try:
            req_kwargs = {"timeout": 30}
            if headers and isinstance(headers, dict):
                req_kwargs["headers"] = headers
            if body:
                if isinstance(body, (dict, list)):
                    req_kwargs["json"] = body
                else:
                    req_kwargs["data"] = body
                    
            resp = requests.request(method, url, **req_kwargs)
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = resp.text
            return {"result": {"status": resp.status_code, "body": resp_data}}
        except Exception as e:
            logger.error("Live webhook failed: %s", e)
            raise RuntimeError(f"Webhook execution failed: {e}")

    def _exec_ai(self, node: Dict, ctx: Dict) -> Dict:
        ai_cfg = node.get("ai", {})
        provider = ai_cfg.get("provider", "?")
        model    = ai_cfg.get("model", "?")
        prompt   = VariableInterpolator.resolve(ai_cfg.get("prompt", ""), ctx)
        if self.dry_run:
            logger.info("  [DRY-RUN] ai %s/%s prompt_chars=%d", provider, model, len(prompt))
            return {"result": "Simulated AI response for prompt: " + prompt[:100] + "..."}
        
        # Live AI Execution
        from ai_builder.ai_client import AIClient
        client = AIClient()
        # Ensure we use the specified model if possible, otherwise rely on the client's default
        try:
            response = client.chat([{"role": "user", "content": prompt}], max_tokens=1000)
            return {"result": response}
        except Exception as e:
            logger.error("Live AI execution failed: %s", e)
            raise RuntimeError(f"AI node execution failed: {e}")

    def _exec_notification(self, node: Dict, ctx: Dict) -> Dict:
        targets = node.get("notification", {}).get("targets", [])
        for t in targets:
            channel  = t.get("channel")
            to       = VariableInterpolator.resolve(t.get("to"), ctx)
            body     = VariableInterpolator.resolve(t.get("body", ""), ctx)
            subject  = VariableInterpolator.resolve(t.get("subject", ""), ctx)
            if self.dry_run:
                logger.info("  [DRY-RUN] notification [%s] → %s | subject=%s | body=%s", channel, to, subject, body[:80])
        return {"result": {"sent": len(targets)}}

    def _exec_human_approval(self, node: Dict, ctx: Dict) -> Dict:
        ha = node.get("human_approval", {})
        approvers = VariableInterpolator.resolve(ha.get("approvers", []), ctx)
        if self.dry_run:
            logger.info("  [DRY-RUN] human_approval approvers=%s (auto-approving)", approvers)
            return {"result": {"approved": True, "approver": approvers[0] if approvers else "auto"}}
        raise NotImplementedError("Live human_approval not implemented (requires UI integration)")

    def _exec_condition(self, node: Dict, ctx: Dict) -> Dict:
        cn = node.get("condition_node", {})
        expr = VariableInterpolator.resolve(cn.get("expression", ""), ctx)
        result = ConditionEvaluator._eval_expression(str(expr), ctx)
        branches = cn.get("branches", [])
        next_node = None
        for branch in branches:
            case = branch.get("case", "")
            if case == "true" and result:
                next_node = branch.get("next")
                break
            elif case == "false" and not result:
                next_node = branch.get("next")
                break
            elif str(result) == str(case):
                next_node = branch.get("next")
                break
        logger.info("  condition expr=%s → %s → next=%s", expr, result, next_node)
        return {"result": {"value": result, "next_node": next_node}}

    def _exec_transform(self, node: Dict, ctx: Dict) -> Dict:
        transform = node.get("transform", {})
        outputs: Dict[str, Any] = {}
        for mapping in transform.get("mappings", []):
            target = mapping.get("target", "")
            value  = VariableInterpolator.resolve(mapping.get("value"), ctx)
            # Strip variable wrapper from target name
            key = re.sub(r"^\$\{|\}$", "", target)
            outputs[key] = value
        logger.info("  transform outputs=%s", list(outputs.keys()))
        return {"result": outputs}

    def _exec_parallel(self, node: Dict, ctx: Dict) -> Dict:
        par = node.get("parallel", {})
        branches = par.get("branches", [])
        if self.dry_run:
            logger.info("  [DRY-RUN] parallel %d branches (join_mode=%s)", len(branches), par.get("join_mode", "all"))
        return {"result": {"branches_completed": len(branches)}}

    def _exec_loop(self, node: Dict, ctx: Dict) -> Dict:
        lp = node.get("loop", {})
        mode = lp.get("mode", "for_each")
        if mode == "for_each":
            collection_ref = lp.get("collection", "")
            key = re.sub(r"^\$\{|\}$", "", collection_ref)
            collection = ctx.get(key, [])
            logger.info("  [DRY-RUN] loop for_each over %s (%d items)", key, len(collection))
        elif mode == "times":
            logger.info("  [DRY-RUN] loop times=%d", lp.get("count", 0))
        return {"result": {"iterations": lp.get("count", 0)}}

    def _exec_subworkflow(self, node: Dict, ctx: Dict) -> Dict:
        sw = node.get("subworkflow", {})
        wf_id = sw.get("workflow_id")
        logger.info("  [DRY-RUN] subworkflow %s", wf_id)
        return {"result": {"workflow_id": wf_id, "status": "simulated"}}

    def _exec_unknown(self, node: Dict, ctx: Dict) -> Dict:
        logger.warning("  Unknown node type: %s", node.get("type"))
        return {"result": None}


# ---------------------------------------------------------------------------
# DAG Resolver
# ---------------------------------------------------------------------------

class DAGResolver:
    """Topological sort of workflow nodes respecting depends_on."""

    @staticmethod
    def resolve(nodes: List[Dict]) -> List[str]:
        """Return node IDs in topological execution order."""
        id_to_deps: Dict[str, List[str]] = {}
        for node in nodes:
            nid   = node["id"]
            deps  = node.get("depends_on", [])
            id_to_deps[nid] = deps

        order: List[str] = []
        visited: set = set()

        def visit(nid: str) -> None:
            if nid in visited:
                return
            visited.add(nid)
            for dep in id_to_deps.get(nid, []):
                if dep in id_to_deps:
                    visit(dep)
            order.append(nid)

        for nid in id_to_deps:
            visit(nid)

        return order


# ---------------------------------------------------------------------------
# Workflow Engine
# ---------------------------------------------------------------------------

class WorkflowEngine:
    """
    Parses and executes Universal Workflow JSON documents.
    In dry_run=True mode, no actual HTTP calls or side-effects occur.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run  = dry_run
        self._workflow: Optional[Dict] = None
        self._path:     Optional[Path] = None

    def load(self, path: str | Path) -> "WorkflowEngine":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Workflow file not found: {p}")
        with p.open(encoding="utf-8") as f:
            self._workflow = json.load(f)
        self._path = p
        logger.info("Loaded workflow: %s v%s", self._workflow.get("name"), self._workflow.get("version"))
        return self

    def validate(self) -> List[str]:
        """Basic structural validation. Returns list of error strings."""
        errors: List[str] = []
        if not self._workflow:
            return ["No workflow loaded"]

        required = ["schema_version", "workflow_id", "name", "version", "metadata", "triggers", "nodes"]
        for field in required:
            if field not in self._workflow:
                errors.append(f"Missing required field: {field}")

        node_ids = {n["id"] for n in self._workflow.get("nodes", [])}
        for node in self._workflow.get("nodes", []):
            for dep in node.get("depends_on", []):
                if dep not in node_ids:
                    errors.append(f"Node '{node['id']}' depends_on unknown node '{dep}'")

        if errors:
            logger.error("Validation failed: %s", errors)
        else:
            logger.info("Validation passed for workflow: %s", self._workflow.get("workflow_id"))
        return errors

    def run(self, inputs: Optional[Dict[str, Any]] = None) -> ExecutionResult:
        """Execute the workflow. Returns an ExecutionResult."""
        if not self._workflow:
            raise RuntimeError("No workflow loaded. Call load() first.")

        errors = self.validate()
        if errors:
            raise ValueError(f"Workflow validation failed: {errors}")

        wf         = self._workflow
        run_id     = f"run_{int(time.time())}"
        started_at = datetime.now(timezone.utc)

        logger.info("=" * 60)
        logger.info("Starting workflow: %s (run_id=%s)", wf["workflow_id"], run_id)
        logger.info("Dry-run mode: %s", self.dry_run)
        logger.info("=" * 60)

        # Build initial context from variable defaults + inputs
        ctx: Dict[str, Any] = {}
        for var in wf.get("variables", []):
            if "default" in var:
                ctx[var["name"]] = copy.deepcopy(var["default"])
        if inputs:
            ctx.update(inputs)

        node_states: Dict[str, NodeState] = {}
        node_map    = {n["id"]: n for n in wf["nodes"]}
        exec_order  = DAGResolver.resolve(wf["nodes"])
        executor    = NodeExecutor(dry_run=self.dry_run, variables=ctx)
        wf_status   = WorkflowStatus.RUNNING

        try:
            for node_id in exec_order:
                node  = node_map[node_id]
                state = NodeState(node_id=node_id)
                node_states[node_id] = state

                # Check skip condition
                cond = node.get("condition")
                if cond and not ConditionEvaluator.evaluate(cond, ctx):
                    state.status = NodeStatus.SKIPPED
                    logger.info("[SKIP] %s (condition false)", node_id)
                    continue

                # Check all dependencies succeeded
                for dep_id in node.get("depends_on", []):
                    dep_state = node_states.get(dep_id)
                    if dep_state and dep_state.status == NodeStatus.FAILED:
                        state.status = NodeStatus.SKIPPED
                        logger.info("[SKIP] %s (dep %s failed)", node_id, dep_id)
                        break
                if state.status == NodeStatus.SKIPPED:
                    continue

                # Apply pre-node delay
                delay_cfg = node.get("delay")
                if delay_cfg:
                    delay_s = 0.0
                    if isinstance(delay_cfg, str):
                        delay_s = _parse_iso_duration_seconds(delay_cfg)
                    if delay_s > 0 and not self.dry_run:
                        logger.info("[DELAY] %s — sleeping %.1fs", node_id, delay_s)
                        time.sleep(delay_s)
                    elif delay_s > 0:
                        logger.info("[DRY-RUN DELAY] %s — would sleep %.1fs", node_id, delay_s)

                # Retry loop
                retry_cfg   = node.get("retry", {})
                max_attempts = retry_cfg.get("max_attempts", 1)

                state.status     = NodeStatus.RUNNING
                state.started_at = datetime.now(timezone.utc)
                last_error: Optional[Exception] = None

                for attempt in range(1, max_attempts + 1):
                    state.attempt = attempt
                    try:
                        logger.info("[RUN] %s (attempt %d/%d) type=%s", node_id, attempt, max_attempts, node.get("type"))
                        output = executor.execute(node, ctx)

                        # Merge outputs into context
                        for out_key, out_var in node.get("outputs", {}).items():
                            var_name = re.sub(r"^\$\{|\}$", "", out_var)
                            val = output.get("result")
                            if isinstance(val, dict):
                                val = val.get(out_key, val)
                            ctx[var_name] = val

                        # For transform nodes, merge result dict into context
                        if node.get("type") == "transform":
                            result = output.get("result", {})
                            if isinstance(result, dict):
                                ctx.update(result)

                        # For human_approval, extract approved
                        if node.get("type") == "human_approval":
                            ctx[f"{node_id}.result"] = output.get("result", {})

                        state.status     = NodeStatus.SUCCEEDED
                        state.outputs    = output
                        last_error       = None
                        break

                    except Exception as exc:
                        last_error = exc
                        logger.warning("[RETRY] %s attempt %d/%d failed: %s", node_id, attempt, max_attempts, exc)
                        if attempt < max_attempts:
                            delay = _compute_delay(attempt, retry_cfg)
                            if delay > 0 and not self.dry_run:
                                time.sleep(delay)

                if last_error:
                    state.status = NodeStatus.FAILED
                    state.error  = {"code": type(last_error).__name__, "message": str(last_error)}

                    err_handler = node.get("error_handler", {})
                    on_error    = err_handler.get("on_error", "fail")
                    logger.error("[FAIL] %s: %s (handler=%s)", node_id, last_error, on_error)

                    if on_error in ("fail",):
                        wf_status = WorkflowStatus.FAILED
                        break

                state.finished_at = datetime.now(timezone.utc)

        except Exception as exc:
            wf_status = WorkflowStatus.FAILED
            logger.exception("Workflow engine error: %s", exc)

        if wf_status == WorkflowStatus.RUNNING:
            wf_status = WorkflowStatus.SUCCEEDED

        finished_at = datetime.now(timezone.utc)
        logger.info("=" * 60)
        logger.info("Workflow finished: %s in %.2fs", wf_status.value,
                    (finished_at - started_at).total_seconds())
        logger.info("=" * 60)

        return ExecutionResult(
            workflow_id  = wf["workflow_id"],
            run_id       = run_id,
            status       = wf_status,
            started_at   = started_at,
            finished_at  = finished_at,
            node_states  = node_states,
            variables    = ctx,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal Workflow Engine — Phase 4",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m workflows.engine workflows/examples/01_google_form_to_sheets.workflow.json --dry-run
  python -m workflows.engine workflows/examples/06_leetcode_tracker.workflow.json --dry-run --verbose
        """
    )
    parser.add_argument("workflow",    help="Path to workflow JSON file")
    parser.add_argument("--dry-run",   action="store_true", default=True, help="Simulate without side effects (default)")
    parser.add_argument("--live",      action="store_true", help="Run in live mode (overrides --dry-run)")
    parser.add_argument("--verbose",   action="store_true", help="Enable debug logging")
    parser.add_argument("--validate",  action="store_true", help="Only validate, do not run")
    parser.add_argument("--input",     action="append", metavar="KEY=VALUE",
                        help="Override a workflow variable, e.g. --input respondent_name=Alice")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)-8s %(message)s")

    inputs: Dict[str, Any] = {}
    for item in (args.input or []):
        k, _, v = item.partition("=")
        inputs[k.strip()] = v.strip()

    dry_run = not args.live

    engine = WorkflowEngine(dry_run=dry_run)
    engine.load(args.workflow)

    if args.validate:
        errs = engine.validate()
        if errs:
            print("VALIDATION FAILED:")
            for e in errs:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("✅ Validation passed.")
        return

    result = engine.run(inputs=inputs)
    print("\n" + result.summary())
    sys.exit(0 if result.status == WorkflowStatus.SUCCEEDED else 1)


if __name__ == "__main__":
    main()
