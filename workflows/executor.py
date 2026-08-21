"""
workflows/executor.py
=====================
Async execution wrapper over the existing WorkflowEngine.

Adds:
  - asyncio-based parallel branch execution
  - live status callbacks (for SSE streaming)
  - structured log persistence to workflows/runs/<wf_id>/<run_id>.json
  - in-memory run registry (RUN_REGISTRY) shared with web_app.py
  - plugin integration for action nodes (falls back to dry-run)

Reuses without modification:
  - WorkflowEngine, DAGResolver, NodeExecutor, VariableInterpolator,
    ConditionEvaluator, NodeStatus, WorkflowStatus, ExecutionResult
  - plugins.sdk.registry.PluginRegistry
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from workflows.engine import (
    ConditionEvaluator,
    DAGResolver,
    ExecutionResult,
    NodeExecutor,
    NodeState,
    NodeStatus,
    VariableInterpolator,
    WorkflowStatus,
    _compute_delay,
    _parse_iso_duration_seconds,
)

logger = logging.getLogger(__name__)

# ── Run registry (shared with web_app.py) ──────────────────────
# run_id → dict with status, logs, node_states, etc.
RUN_REGISTRY: Dict[str, Dict[str, Any]] = {}
_registry_lock = threading.Lock()

# ── Log persistence root ────────────────────────────────────────
_RUNS_DIR = Path("workflows") / "runs"


# ---------------------------------------------------------------------------
# Log persistence helpers
# ---------------------------------------------------------------------------

def _run_dir(wf_id: str) -> Path:
    d = _RUNS_DIR / wf_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_run(wf_id: str, run_id: str, record: Dict) -> None:
    """Write run record to disk (best-effort, never raises)."""
    try:
        p = _run_dir(wf_id) / f"{run_id}.json"
        with p.open("w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, default=str)
    except Exception as e:
        logger.debug("Could not persist run log: %s", e)


def load_run(wf_id: str, run_id: str) -> Optional[Dict]:
    """Load a persisted run record from disk."""
    p = _RUNS_DIR / wf_id / f"{run_id}.json"
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def list_runs(wf_id: str) -> List[Dict]:
    """List all persisted run summaries for a workflow, newest first."""
    d = _RUNS_DIR / wf_id
    if not d.exists():
        return []
    runs = []
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with p.open(encoding="utf-8") as f:
                rec = json.load(f)
            runs.append({
                "run_id":     rec.get("run_id"),
                "status":     rec.get("status"),
                "started_at": rec.get("started_at"),
                "finished_at":rec.get("finished_at"),
                "node_count": len(rec.get("node_states", {})),
            })
        except Exception:
            pass
    return runs[:50]


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

def _make_record(wf_id: str, run_id: str, wf_name: str) -> Dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "run_id":      run_id,
        "workflow_id": wf_id,
        "workflow_name": wf_name,
        "status":      WorkflowStatus.PENDING.value,
        "started_at":  now,
        "finished_at": None,
        "node_states": {},
        "logs":        [],
        "variables":   {},
        "error":       None,
    }


def _log(record: Dict, level: str, msg: str) -> None:
    entry = {
        "ts":    datetime.now(timezone.utc).isoformat(),
        "level": level,
        "msg":   msg,
    }
    record["logs"].append(entry)


def _update_node(record: Dict, node_id: str, **kwargs) -> None:
    ns = record["node_states"].setdefault(node_id, {
        "node_id": node_id, "status": "pending",
        "started_at": None, "finished_at": None,
        "attempt": 0, "outputs": {}, "error": None,
    })
    ns.update(kwargs)


# ---------------------------------------------------------------------------
# Async parallel executor
# ---------------------------------------------------------------------------

# ── Shared plugin registry (loaded once per process) ───────────
_PLUGIN_REGISTRY = None
_plugin_lock     = threading.Lock()


def _get_plugin_registry():
    """Return a singleton PluginRegistry, loading plugins on first call."""
    global _PLUGIN_REGISTRY
    if _PLUGIN_REGISTRY is None:
        with _plugin_lock:
            if _PLUGIN_REGISTRY is None:
                try:
                    from plugins.sdk.registry import PluginRegistry
                    reg = PluginRegistry()
                    reg.load_all()
                    _PLUGIN_REGISTRY = reg
                    logger.info("PluginRegistry loaded: %d plugin(s)", len(reg))
                except Exception as e:
                    logger.warning("PluginRegistry unavailable: %s", e)
                    _PLUGIN_REGISTRY = False  # sentinel so we don't retry forever
    return _PLUGIN_REGISTRY if _PLUGIN_REGISTRY else None


def _run_node_via_plugin(
    node: Dict,
    ctx: Dict,
    run_id: str,
    dry_run: bool,
    plugin_configs: Dict,
) -> Dict:
    """
    Execute a node via the PluginRegistry when the node's action.integration
    matches a registered plugin ID.  Falls back to NodeExecutor dry-run path
    when no plugin is found or dry_run is True.

    Supported node types: action, webhook, notification.
    All others fall through to NodeExecutor.
    """
    from workflows.engine import NodeExecutor, VariableInterpolator

    node_type = node.get("type", "action")
    registry  = None if dry_run else _get_plugin_registry()

    # ── action / webhook nodes ──────────────────────────────────
    if node_type in ("action", "webhook") and registry:
        action_cfg  = node.get("action") or node.get("webhook") or {}
        integration = action_cfg.get("integration", "").lower().replace(" ", "_")
        operation   = action_cfg.get("operation", "get")
        params      = VariableInterpolator.resolve(action_cfg.get("params", {}), ctx)

        # Map common aliases to plugin IDs
        _alias = {
            "gmail": "email", "outlook": "email",
            "google_sheets": "google", "sheets": "google",
            "openweathermap": "weather",
            "http": "rest_api", "https": "rest_api",
        }
        plugin_id = _alias.get(integration, integration)

        if plugin_id in registry:
            from plugins.sdk.context import PluginContext
            user_config = plugin_configs.get(plugin_id, {})
            pctx = PluginContext(
                plugin_id = plugin_id,
                run_id    = run_id,
                node_id   = node.get("id", "unknown"),
                config    = user_config.get("config", {}),
                _secrets  = user_config.get("secrets", {}),
                dry_run   = dry_run,
            )
            plugin = registry.get(plugin_id)
            result = plugin.execute_action(operation, pctx, params)
            return {"result": result.data, "success": result.success, "error": result.error}

    # ── notification node → email plugin ───────────────────────
    if node_type == "notification" and registry and "email" in registry:
        targets = node.get("notification", {}).get("targets", [])
        sent    = 0
        errors  = []
        for t in targets:
            channel = t.get("channel", "")
            if channel != "email":
                continue
            from plugins.sdk.context import PluginContext
            user_config = plugin_configs.get("email", {})
            pctx = PluginContext(
                plugin_id = "email",
                run_id    = run_id,
                node_id   = node.get("id", "unknown"),
                config    = user_config.get("config", {}),
                _secrets  = user_config.get("secrets", {}),
                dry_run   = dry_run,
            )
            resolved_to   = VariableInterpolator.resolve(t.get("to", ""), ctx)
            resolved_subj = VariableInterpolator.resolve(t.get("subject", ""), ctx)
            resolved_body = VariableInterpolator.resolve(t.get("body", ""), ctx)
            try:
                plugin = registry.get("email")
                res = plugin.execute_action("send", pctx, {
                    "to": resolved_to, "subject": resolved_subj,
                    "body": resolved_body, "html": False,
                })
                if res.success:
                    sent += 1
                else:
                    errors.append(res.error)
            except Exception as e:
                errors.append(str(e))
        return {"result": {"sent": sent, "errors": errors}}

    # ── human_approval node ─────────────────────────────────────
    if node_type == "human_approval":
        cfg      = node.get("human_approval") or {}
        timeout_s = int(cfg.get("timeout_seconds", 3600))  # 1 hour default
        node_id  = node.get("id", "unknown")

        # Cross-process: poll disk for approval file written by web_app approve/reject APIs
        import json as _json, time as _time
        appr_dir = Path("workflows/approvals")
        appr_dir.mkdir(parents=True, exist_ok=True)
        appr_file = appr_dir / f"{run_id}.json"

        start_t = _time.time()
        decision = None
        while _time.time() - start_t < timeout_s:
            if appr_file.exists():
                try:
                    decision = _json.loads(appr_file.read_text(encoding="utf-8"))
                    appr_file.unlink(missing_ok=True)
                except Exception:
                    pass
                break
            _time.sleep(1)

        if not decision:
            raise TimeoutError(f"Human approval timed out after {timeout_s}s")
        if not decision.get("approved"):
            raise RuntimeError(f"Workflow rejected by {decision.get('approver')}: {decision.get('comment')}")
        return {"result": {"approved": True, "approver": decision.get("approver"), "comment": decision.get("comment")}}

    # ── subworkflow node ─────────────────────────────────────────
    if node_type == "subworkflow":
        cfg    = node.get("subworkflow") or {}
        sub_id = cfg.get("workflow_id")
        if not sub_id:
            raise ValueError("subworkflow node missing 'workflow_id'")
        sub_wf_path = Path("workflows") / "saved" / f"{sub_id}.json"
        if not sub_wf_path.exists():
            raise FileNotFoundError(f"Subworkflow '{sub_id}' not found")
        with sub_wf_path.open(encoding="utf-8") as f:
            sub_wf = json.load(f)
        # Run synchronously inside current thread (blocking, with timeout guard)
        sub_inputs = dict(ctx)  # pass current context as inputs
        sub_run_id = f"sub_{uuid.uuid4().hex[:10]}"
        sub_record = _make_record(sub_id, sub_run_id, sub_wf.get("name", "Subworkflow"))
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _execute_async(sub_wf, sub_run_id, sub_record, dry_run, sub_inputs, None, plugin_configs)
            )
        finally:
            loop.close()
        if sub_record["status"] != WorkflowStatus.SUCCEEDED.value:
            raise RuntimeError(f"Subworkflow '{sub_id}' failed: {sub_record.get('error')}")
        return {"result": {"sub_run_id": sub_run_id, "status": sub_record["status"],
                           "outputs": {k: v.get("outputs") for k, v in sub_record.get("node_states", {}).items()}}}

    # ── Fallback: NodeExecutor (respects live vs dry-run mode) ────
    engine_executor = NodeExecutor(dry_run=dry_run, variables=ctx)
    return engine_executor.execute(node, ctx)


async def _run_node_async(
    node: Dict,
    ctx: Dict,
    executor: NodeExecutor,
    dry_run: bool,
    run_id: str = "unknown",
    plugin_configs: Dict = None,
) -> Dict:
    """Run a single node: prefer PluginRegistry, fall back to NodeExecutor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _run_node_via_plugin, node, ctx, run_id, dry_run, plugin_configs or {}
    )



async def _run_parallel_branches(
    branches: List[Dict],
    ctx: Dict,
    executor: NodeExecutor,
    record: Dict,
    dry_run: bool,
    run_id: str = "unknown",
    plugin_configs: Dict = None,
) -> Dict[str, Any]:
    """Execute parallel branches concurrently, collect outputs."""
    plugin_configs = plugin_configs or {}
    async def run_branch(branch: Dict) -> Dict:
        bname   = branch.get("name", "branch")
        results = {}
        for sub_node in branch.get("nodes", []):
            nid  = sub_node.get("id", f"sub_{uuid.uuid4().hex[:6]}")
            _update_node(record, nid, status="running",
                         started_at=datetime.now(timezone.utc).isoformat())
            _log(record, "info", f"  [PARALLEL:{bname}] Running {nid}")
            try:
                out = await _run_node_async(sub_node, ctx, executor, dry_run,
                                            run_id=run_id, plugin_configs=plugin_configs)
                _update_node(record, nid, status="success",
                             outputs=out,
                             finished_at=datetime.now(timezone.utc).isoformat())
                results[nid] = out
            except Exception as e:
                _update_node(record, nid, status="failed", error=str(e),
                             finished_at=datetime.now(timezone.utc).isoformat())
                _log(record, "error", f"  [PARALLEL:{bname}] {nid} failed: {e}")
        return results

    tasks   = [run_branch(b) for b in branches]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    merged: Dict[str, Any] = {}
    for r in results:
        if isinstance(r, dict):
            merged.update(r)
    return merged


# ---------------------------------------------------------------------------
# Main async executor
# ---------------------------------------------------------------------------

async def _execute_async(
    workflow: Dict,
    run_id:   str,
    record:   Dict,
    dry_run:  bool,
    inputs:   Optional[Dict],
    on_update: Optional[Callable],
    plugin_configs: Dict = None,
) -> None:
    """Core async execution loop — drives the DAG."""
    plugin_configs = plugin_configs or {}
    if not plugin_configs and not dry_run:
        # Load from disk if not injected (e.g. from trigger runtime)
        plugin_configs = _load_plugin_configs()

    def notify():
        if on_update:
            try:
                on_update(run_id, copy.deepcopy(record))
            except Exception:
                pass

    try:
        record["status"] = WorkflowStatus.RUNNING.value
        notify()

        nodes    = workflow.get("nodes", [])
        node_map = {n["id"]: n for n in nodes}
        exec_order = DAGResolver.resolve(nodes)

        # Build initial context
        ctx: Dict[str, Any] = {}
        for var in workflow.get("variables", []):
            if "default" in var:
                ctx[var["name"]] = copy.deepcopy(var["default"])
        if inputs:
            ctx.update(inputs)

        executor = NodeExecutor(dry_run=dry_run, variables=ctx)

        _log(record, "info", f"Starting workflow '{workflow.get('name')}' run_id={run_id}")
        _log(record, "info", f"Execution order: {exec_order}")
        notify()

        wf_failed = False

        for node_id in exec_order:
            if wf_failed:
                break

            node = node_map.get(node_id)
            if not node:
                continue

            # Skip if a dependency failed (but respect fallback_node_id routing)
            skip_reason = None
            for dep_id in node.get("depends_on", []):
                dep_ns = record["node_states"].get(dep_id, {})
                dep_status = dep_ns.get("status")
                if dep_status == "failed":
                    dep_node = node_map.get(dep_id, {})
                    fallback_id = (dep_node.get("error_handler") or {}).get("fallback_node_id")
                    if fallback_id == node_id:
                        continue  # allow fallback to execute when parent failed
                    skip_reason = f"dependency '{dep_id}' failed"
                    break
                elif dep_status in ("success", "skipped"):
                    dep_node = node_map.get(dep_id, {})
                    fallback_id = (dep_node.get("error_handler") or {}).get("fallback_node_id")
                    if fallback_id == node_id:
                        skip_reason = "primary succeeded, skipping fallback"
                        break

            if skip_reason:
                _update_node(record, node_id, status="skipped")
                _log(record, "info", f"[SKIP] {node_id}: {skip_reason}")
                notify()
                continue

            # Pre-node condition check
            cond = node.get("condition")
            if cond and not ConditionEvaluator.evaluate(cond, ctx):
                _update_node(record, node_id, status="skipped")
                _log(record, "info", f"[SKIP] {node_id}: condition false")
                notify()
                continue

            # Pre-node delay
            delay_cfg = node.get("delay")
            if delay_cfg:
                delay_s = _parse_iso_duration_seconds(
                    delay_cfg.get("duration") if isinstance(delay_cfg, dict) else delay_cfg
                )
                if delay_s > 0:
                    _log(record, "info", f"[DELAY] {node_id}: {delay_s}s")
                    if not dry_run:
                        await asyncio.sleep(delay_s)

            # --- Parallel node special handling ---
            node_type = node.get("type", "action")
            if node_type == "parallel":
                _update_node(record, node_id, status="running",
                             started_at=datetime.now(timezone.utc).isoformat())
                _log(record, "info", f"[PARALLEL] {node_id}: starting branches")
                notify()
                branches = node.get("parallel", {}).get("branches", [])
                outputs  = await _run_parallel_branches(
                    branches, ctx, executor, record, dry_run,
                    run_id=run_id, plugin_configs=plugin_configs
                )
                ctx.update(outputs)
                _update_node(record, node_id, status="success",
                             outputs={"branches": len(branches)},
                             finished_at=datetime.now(timezone.utc).isoformat())
                _log(record, "info", f"[PARALLEL] {node_id}: all branches done")
                notify()
                continue

            # --- Regular node with retry ---
            retry_cfg    = node.get("retry") or {}
            max_attempts = int(retry_cfg.get("max_attempts", 1))
            timeout_s    = _parse_iso_duration_seconds(
                (node.get("timeout") or {}).get("duration")
            ) or None

            _update_node(record, node_id, status="running",
                         started_at=datetime.now(timezone.utc).isoformat())
            notify()

            last_error: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                _update_node(record, node_id, attempt=attempt)
                _log(record, "info",
                     f"[RUN] {node_id} attempt {attempt}/{max_attempts} type={node_type}")
                notify()

                try:
                    if timeout_s and timeout_s > 0:
                        output = await asyncio.wait_for(
                            _run_node_async(node, ctx, executor, dry_run,
                                            run_id=run_id, plugin_configs=plugin_configs),
                            timeout=timeout_s,
                        )
                    else:
                        output = await _run_node_async(
                            node, ctx, executor, dry_run,
                            run_id=run_id, plugin_configs=plugin_configs
                        )

                    # Merge outputs into context
                    ctx[node_id] = output.get("result", output)
                    for out_key, out_var in node.get("outputs", {}).items():
                        var_name = re.sub(r"^\$\{|\}$", "", out_var)
                        val = output.get("result")
                        if isinstance(val, dict):
                            val = val.get(out_key, val)
                        ctx[var_name] = val

                    # Transform merges into context directly
                    if node_type == "transform":
                        result = output.get("result", {})
                        if isinstance(result, dict):
                            ctx.update(result)

                    _update_node(record, node_id, status="success",
                                 outputs=output,
                                 finished_at=datetime.now(timezone.utc).isoformat())
                    _log(record, "info", f"[OK] {node_id}")
                    last_error = None
                    break

                except asyncio.TimeoutError:
                    last_error = TimeoutError(f"Node timed out after {timeout_s}s")
                    _log(record, "warning", f"[TIMEOUT] {node_id}")
                    break  # don't retry on timeout

                except Exception as exc:
                    last_error = exc
                    _log(record, "warning",
                         f"[RETRY] {node_id} attempt {attempt}/{max_attempts}: {exc}")
                    if attempt < max_attempts:
                        delay = _compute_delay(attempt, retry_cfg)
                        if delay > 0 and not dry_run:
                            await asyncio.sleep(delay)

                notify()

            if last_error:
                err_type = "timed_out" if isinstance(last_error, TimeoutError) else "failed"
                _update_node(record, node_id, status=err_type,
                             error=str(last_error),
                             finished_at=datetime.now(timezone.utc).isoformat())
                _log(record, "error", f"[FAIL] {node_id}: {last_error}")

                on_error = (node.get("error_handler") or {}).get("on_error", "fail")
                if on_error == "fail":
                    wf_failed = True
                else:
                    _log(record, "info",
                         f"[HANDLER] {node_id} on_error={on_error}, continuing")

            notify()

        # Final status
        if wf_failed:
            record["status"] = WorkflowStatus.FAILED.value
        else:
            all_states = [v.get("status") for v in record["node_states"].values()]
            if any(s == "failed" for s in all_states):
                record["status"] = WorkflowStatus.FAILED.value
            else:
                record["status"] = WorkflowStatus.SUCCEEDED.value

    except Exception as exc:
        record["status"] = WorkflowStatus.FAILED.value
        record["error"]  = str(exc)
        _log(record, "error", f"Engine error: {exc}")
        logger.exception("Workflow executor error: %s", exc)

    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["variables"]   = {}  # don't expose secrets
        notify()

        wf_id = record.get("workflow_id", "unknown")
        _persist_run(wf_id, run_id, record)
        _log(record, "info",
             f"Run complete: {record['status']} at {record['finished_at']}")


_PLUGIN_CFG_PATH = Path("workflows") / "plugin_configs.json"


def _load_plugin_configs() -> Dict:
    """Load per-plugin credentials stored by the Plugin Settings UI."""
    if not _PLUGIN_CFG_PATH.exists():
        return {}
    try:
        with _PLUGIN_CFG_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_plugin_configs(configs: Dict) -> None:
    """Persist plugin credentials (called from web_app.py)."""
    _PLUGIN_CFG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _PLUGIN_CFG_PATH.open("w", encoding="utf-8") as f:
        json.dump(configs, f, indent=2)


# ---------------------------------------------------------------------------
# Public API — called from web_app.py
# ---------------------------------------------------------------------------

def start_run(
    workflow:       Dict,
    dry_run:        bool = True,
    inputs:         Optional[Dict] = None,
    on_update:      Optional[Callable] = None,
    plugin_configs: Optional[Dict] = None,
) -> str:
    """
    Kick off a workflow execution in a background thread.
    Returns run_id immediately.
    """
    run_id   = f"run_{uuid.uuid4().hex[:12]}"
    wf_id    = workflow.get("id") or workflow.get("workflow_id", "unknown")
    wf_name  = workflow.get("name", "Unnamed")
    p_cfg    = plugin_configs or _load_plugin_configs()

    record = _make_record(wf_id, run_id, wf_name)

    with _registry_lock:
        RUN_REGISTRY[run_id] = record

    # Persist record to disk so worker process can load it if needed
    _persist_run(wf_id, run_id, record)

    # Try Huey queue first (if consumer process is running), else use thread
    _use_thread = os.environ.get("WORKFLOW_EXECUTOR", "auto").lower() in ("thread", "inline")
    if not _use_thread:
        try:
            from workflows.queue import execute_workflow_task
            execute_workflow_task(workflow, run_id, dry_run, inputs or {}, p_cfg)
            logger.info("Enqueued run %s to Huey", run_id)
            return run_id
        except Exception as _q_err:
            logger.warning("Huey enqueue failed (%s), falling back to thread", _q_err)
            _use_thread = True

    if _use_thread:
        def _thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    _execute_async(workflow, run_id, record, dry_run, inputs, None, p_cfg)
                )
            finally:
                loop.close()

        t = threading.Thread(target=_thread_target, daemon=True, name=f"wf-run-{run_id}")
        t.start()
        logger.info("Run %s started in background thread", run_id)

    return run_id


def get_run(run_id: str) -> Optional[Dict]:
    """Get run record from memory (fast) or disk (fallback)."""
    with _registry_lock:
        rec = RUN_REGISTRY.get(run_id)
    return rec

