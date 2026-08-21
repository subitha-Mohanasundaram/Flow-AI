from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
import html
from typing import Any, Dict

# Load environment variables from .env (shared config across web/worker/MCP server).
try:  # pragma: no cover
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

logger = logging.getLogger(__name__)

from assignment_intel.db import get_evaluation, list_evaluations_for_user, list_recent_evaluations
from assignment_intel.eval_service import enqueue_and_run
from assignment_intel.mcp_layer import ToolCall, call_tool
from assignment_intel.storage import save_submission_bytes
from assignment_intel.storage import save_submission_text
from assignment_intel.storage import safe_slug


PRESENCE: dict[str, dict[str, int]] = {}


def _presence_cleanup(now_ms: int, ttl_ms: int = 45_000) -> None:
    for page, clients in list(PRESENCE.items()):
        for cid, ts in list(clients.items()):
            if now_ms - int(ts) > ttl_ms:
                clients.pop(cid, None)
        if not clients:
            PRESENCE.pop(page, None)


def _presence_count(page: str) -> int:
    now_ms = int(time.time() * 1000)
    _presence_cleanup(now_ms)
    return len(PRESENCE.get(page, {}))


def _presence_widget(page: str) -> str:
    page = page or "global"
    return f"""
    <div class="card" style="margin-bottom:12px; display:flex; justify-content:space-between; align-items:center">
      <div><strong>Live:</strong> <span id="active-count">...</span> active</div>
      <div><code>{_esc(page)}</code></div>
    </div>
    <script>
      (function() {{
        const page = {json.dumps(page)};
        const key = "client_id";
        let cid = localStorage.getItem(key);
        if (!cid) {{
          cid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Math.random()).slice(2);
          localStorage.setItem(key, cid);
        }}
        async function ping() {{
          try {{
            const r = await fetch("/presence/ping", {{
              method: "POST",
              headers: {{"Content-Type":"application/json"}},
              body: JSON.stringify({{page: page, client_id: cid}})
            }});
            const j = await r.json();
            const el = document.getElementById("active-count");
            if (el) el.textContent = String(j.count ?? "?");
          }} catch (e) {{}}
        }}
        ping();
        setInterval(ping, 5000);
      }})();
    </script>
    """


def _current_user(request: "Request") -> dict[str, Any] | None:
    try:
        from assignment_intel.auth import decode_session_token
        from assignment_intel.db import get_user_by_id

        # 1. Try Bearer token from Authorization header (React frontend)
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

        # 2. Fall back to cookie (Jinja2 frontend)
        if not token:
            token = request.cookies.get("session")

        if not token:
            return None
        payload = decode_session_token(token)
        sub = payload.get("sub")
        if not sub:
            return None
        user = get_user_by_id(int(sub))
        return user if isinstance(user, dict) else None
    except Exception:
        return None


def _require_role(request: "Request", roles: set[str]) -> dict[str, Any] | "RedirectResponse":
    user = _current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    role = str(user.get("role") or "").lower()
    if role not in roles:
        return RedirectResponse(url="/login", status_code=303)
    return user


def _nav(request: "Request") -> str:
    user = _current_user(request)
    if not user:
        if (os.environ.get("DEMO_AUTH", "") or "").strip() == "1":
            return (
                "<div class='card' style='margin-bottom:12px'>"
                "<a href='/login'>Demo Login</a> | <a href='/assignments'>Assignments</a>"
                "</div>"
            )
        return (
            "<div class='card' style='margin-bottom:12px'>"
            "<a href='/login'>Login</a> | <a href='/register'>Register</a> | <a href='/assignments'>Assignments</a>"
            "</div>"
        )

    uname = _esc(user.get("username") or "")
    role = _esc(user.get("role") or "")
    links = ["<a href='/assignments'>Assignments</a>", "<a href='/me'>My Dashboard</a>"]
    if str(user.get("role") or "").lower() in {"instructor", "admin"}:
        links.append("<a href='/instructor/assignments'>Instructor</a>")
        links.append("<a href='/instructor/analytics'>Analytics</a>")
    if str(user.get("role") or "").lower() == "admin":
        links.append("<a href='/admin'>Admin</a>")
    links.append("<form action='/auth/logout' method='post' style='display:inline; margin-left:10px'><button type='submit'>Logout</button></form>")
    return (
        "<div class='card' style='margin-bottom:12px; display:flex; justify-content:space-between; align-items:center'>"
        f"<div><strong>{uname}</strong> <span class='badge'>{role}</span></div>"
        f"<div>{' | '.join(links)}</div>"
        "</div>"
    )


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, Segoe UI, Arial; max-width: 860px; margin: 32px auto; padding: 0 16px; }}
    .card {{ border: 1px solid #ddd; border-radius: 12px; padding: 16px; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
    .kpi {{ flex: 1; min-width: 160px; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px; background: #fafafa; }}
    .kpi .label {{ font-size: 12px; color: #4b5563; margin: 0; }}
    .kpi .value {{ font-size: 20px; font-weight: 800; margin: 6px 0 0 0; }}
    .badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; border: 1px solid #ddd; background: #f3f4f6; }}
    .badge.ok {{ border-color: #bbf7d0; background: #dcfce7; color: #14532d; }}
    .badge.warn {{ border-color: #fde68a; background: #fef3c7; color: #92400e; }}
    .badge.bad {{ border-color: #fecaca; background: #fee2e2; color: #7f1d1d; }}
    details {{ border: 1px solid #e5e7eb; border-radius: 12px; padding: 10px 12px; background: #fff; }}
    details > summary {{ cursor: pointer; font-weight: 700; }}
    pre {{ margin: 10px 0 0 0; background: #0b1020; color: #e5e7eb; padding: 12px; border-radius: 10px; overflow: auto; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e5e7eb; font-size: 13px; vertical-align: top; }}
    th {{ background: #f9fafb; }}
    label {{ display:block; margin-top: 10px; font-weight: 600; }}
    input {{ width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px; }}
    button {{ margin-top: 14px; padding: 10px 14px; border: 0; border-radius: 10px; background: #111827; color: #fff; cursor: pointer; }}
    code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }}
    a {{ color: #1d4ed8; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {body}
</body>
</html>"""


def _render_raw(request: "Request", title: str, body_html: str):
    user = _current_user(request)
    if templates is None:
        # Fallback if Jinja isn't installed yet.
        return HTMLResponse(_html_page(title, body_html))
    try:
        from markupsafe import Markup

        body_html = Markup(body_html)
    except Exception:
        pass
    return templates.TemplateResponse(
        "raw.html",
        {
            "request": request,
            "title": title,
            "user": user,
            "body_html": body_html,
        },
    )


def _esc(s: object) -> str:
    return html.escape("" if s is None else str(s))


def _badge(text: str, kind: str) -> str:
    kind = kind if kind in {"ok", "warn", "bad"} else "warn"
    return f"<span class='badge {kind}'>{_esc(text)}</span>"


def _kpi(label: str, value: str, badge_html: str = "") -> str:
    return f"<div class='kpi'><p class='label'>{_esc(label)}</p><p class='value'>{_esc(value)} {badge_html}</p></div>"


def _guess_status(score: float, *, ok: bool = True) -> str:
    if not ok:
        return "bad"
    if score >= 90:
        return "ok"
    if score >= 60:
        return "warn"
    return "bad"


def _render_eval_report(obj: dict) -> str:
    student = obj.get("student_name", "")
    pid = obj.get("problem_id", "")
    lang = obj.get("language", "")
    submitted_at = obj.get("submitted_at", "")
    analysis = obj.get("analysis") if isinstance(obj.get("analysis"), dict) else {}
    results = analysis.get("results") if isinstance(analysis.get("results"), dict) else {}
    complexity = analysis.get("complexity") if isinstance(analysis.get("complexity"), dict) else {}
    score = float(results.get("score", 0.0) or 0.0)

    total = int(results.get("total_test_cases", 0) or 0)
    passed = int(results.get("passed_cases", 0) or 0)
    visible = results.get("visible") if isinstance(results.get("visible"), dict) else {}
    hidden = results.get("hidden") if isinstance(results.get("hidden"), dict) else {}
    anti = results.get("anti_cheat") if isinstance(results.get("anti_cheat"), dict) else {}
    plag = results.get("plagiarism") if isinstance(results.get("plagiarism"), dict) else {}

    anti_ok = anti.get("passed") is True
    plag_ok = plag.get("detected") is False
    status_kind = _guess_status(score, ok=True)

    username_slug = safe_slug(str(student))
    submission_path = obj.get("submission_path") or ""
    header = (
        "<div class='card'>"
        f"<p><strong>Student:</strong> <code>{_esc(student)}</code> &nbsp; "
        f"<strong>Problem:</strong> <code>{_esc(pid)}</code> &nbsp; "
        f"<strong>Language:</strong> <code>{_esc(lang)}</code></p>"
        f"<p><strong>Submitted at:</strong> <code>{_esc(submitted_at)}</code></p>"
        f"<p><strong>Score:</strong> {_badge(str(score), status_kind)} &nbsp; "
        f"<strong>Passed:</strong> <code>{passed}/{total}</code></p>"
        f"<p><strong>Submission:</strong> <code>{_esc(submission_path)}</code></p>"
        f"<p><strong>Edit & Resubmit:</strong> <a href='/editor?username={_esc(username_slug)}&problem_id={_esc(pid)}'>open editor</a></p>"
        "</div>"
    )

    kpis = (
        "<div class='row' style='margin-top:12px'>"
        + _kpi(
            "Visible",
            f"{int(visible.get('passed', 0) or 0)}/{int(visible.get('total', 0) or 0)}",
            _badge(f"{visible.get('weighted_contribution', 0)} pts", "ok"),
        )
        + _kpi(
            "Hidden",
            f"{int(hidden.get('passed', 0) or 0)}/{int(hidden.get('total', 0) or 0)}",
            _badge(f"{hidden.get('weighted_contribution', 0)} pts", "ok"),
        )
        + _kpi("Anti-Cheat", "PASS" if anti_ok else "FAIL", _badge("ok" if anti_ok else "blocked", "ok" if anti_ok else "bad"))
        + _kpi(
            "Plagiarism",
            "CLEAR" if plag_ok else "FLAGGED",
            _badge(f"risk {plag.get('risk_score', 0)}", "ok" if plag_ok else "bad"),
        )
        + "</div>"
    )

    result_file = results.get("result_file")
    result_json = results.get("result_json")
    stdout = results.get("stdout", "")
    stderr = results.get("stderr", "")

    links = "<div style='margin-top:10px'>"
    if result_json:
        links += f"<p><strong>Result JSON:</strong> <a href='/report?path={_esc(Path(str(result_json)).as_posix())}'>{_esc(Path(str(result_json)).name)}</a></p>"
    if result_file:
        links += f"<p><strong>Result TXT:</strong> <code>{_esc(result_file)}</code></p>"
    links += "</div>"

    # Tool status list (if present)
    tool_rows = []
    tools = obj.get("tools", [])
    if isinstance(tools, list) and tools:
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = t.get("tool", "")
            ok = t.get("ok", True)
            err = t.get("error")
            tool_rows.append(
                "<tr>"
                f"<td><code>{_esc(name)}</code></td>"
                f"<td>{_badge('OK' if ok else 'FAIL', 'ok' if ok else 'bad')}</td>"
                f"<td><code>{_esc(err) if err else ''}</code></td>"
                "</tr>"
            )
    tools_table = ""
    if tool_rows:
        tools_table = (
            "<details style='margin-top:12px' open>"
            "<summary>Tool Results</summary>"
            "<table><thead><tr><th>Tool</th><th>Status</th><th>Error</th></tr></thead>"
            f"<tbody>{''.join(tool_rows)}</tbody></table>"
            "</details>"
        )

    # Error analysis panel
    err_blocks = []
    if stderr:
        err_blocks.append("<details style='margin-top:12px' open><summary>Stderr</summary>" f"<pre>{_esc(stderr)}</pre></details>")
    if stdout:
        err_blocks.append("<details style='margin-top:12px'><summary>Stdout (test runner output)</summary>" f"<pre>{_esc(stdout)}</pre></details>")

    # Visible case results (IO-mode)
    case_rows = ""
    cr = results.get("case_results") if isinstance(results.get("case_results"), list) else []
    if isinstance(cr, list) and cr:
        for item in cr:
            if not isinstance(item, dict):
                continue
            if str(item.get("visibility")) != "visible":
                continue
            case_rows += (
                "<tr>"
                f"<td>{_badge('PASS' if item.get('passed') else 'FAIL', 'ok' if item.get('passed') else 'bad')}</td>"
                f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(item.get('input'))}</pre></td>"
                f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(item.get('expected'))}</pre></td>"
                f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(item.get('actual'))}</pre></td>"
                "</tr>"
            )
    cases_panel = ""
    if case_rows:
        cases_panel = (
            "<details style='margin-top:12px' open>"
            "<summary>Visible Test Case Results</summary>"
            "<table><thead><tr><th>Status</th><th>Input</th><th>Expected</th><th>Actual</th></tr></thead>"
            f"<tbody>{case_rows}</tbody></table>"
            "</details>"
        )

    # Complexity/feedback panels
    cx = (
        "<details style='margin-top:12px'>"
        "<summary>Complexity & Quality</summary>"
        f"<p><strong>Time:</strong> <code>{_esc(complexity.get('time_complexity',''))}</code></p>"
        f"<p><strong>Space:</strong> <code>{_esc(complexity.get('space_complexity',''))}</code></p>"
        "</details>"
    )
    feedback = (
        "<details style='margin-top:12px' open>"
        "<summary>Feedback & Hints</summary>"
        f"<p><strong>Feedback:</strong> {_esc(obj.get('feedback',''))}</p>"
        "<p><strong>Hints:</strong></p>"
        "<ul>"
        + "".join(f"<li>{_esc(h)}</li>" for h in (obj.get("hints") or []) if isinstance(h, (str, int, float)))
        + "</ul>"
        "</details>"
    )

    raw = json.dumps(obj, indent=2)
    raw_panel = (
        "<details style='margin-top:12px'>"
        "<summary>Raw JSON</summary>"
        f"<pre>{_esc(raw)}</pre>"
        "</details>"
    )

    return header + kpis + links + tools_table + cases_panel + "".join(err_blocks) + cx + feedback + raw_panel


def _render_agent_report(obj: dict) -> str:
    # Supports either {agents:[...], final_report:{...}} or local runner report shape.
    agents = obj.get("agents")
    final_report = obj.get("final_report") if isinstance(obj.get("final_report"), dict) else None
    if not isinstance(agents, list) and isinstance(obj.get("planner"), dict):
        # local multi-agent runner payload
        final_report = {"score": obj.get("score", 0), "report": obj}
        agents = []

    score = 0.0
    if isinstance(final_report, dict):
        score = float(final_report.get("score", 0.0) or 0.0)

    header = "<div class='card'>" f"<p><strong>Agent Score:</strong> {_badge(str(score), _guess_status(score))}</p>" "</div>"

    agent_rows = []
    if isinstance(agents, list):
        for a in agents:
            if not isinstance(a, dict):
                continue
            agent_rows.append(
                "<tr>"
                f"<td><code>{_esc(a.get('role',''))}</code></td>"
                f"<td><code>{_esc(a.get('type',''))}</code></td>"
                f"<td><pre style='margin:0'>{_esc(json.dumps(a.get('payload',{}), indent=2)[:4000])}</pre></td>"
                "</tr>"
            )
    agents_panel = ""
    if agent_rows:
        agents_panel = (
            "<details style='margin-top:12px' open>"
            "<summary>Agents (messages)</summary>"
            "<table><thead><tr><th>Role</th><th>Type</th><th>Payload (truncated)</th></tr></thead>"
            f"<tbody>{''.join(agent_rows)}</tbody></table>"
            "</details>"
        )

    final_panel = ""
    if isinstance(final_report, dict):
        final_panel = (
            "<details style='margin-top:12px' open>"
            "<summary>Final Report</summary>"
            f"<pre>{_esc(json.dumps(final_report, indent=2)[:12000])}</pre>"
            "</details>"
        )

    raw = (
        "<details style='margin-top:12px'>"
        "<summary>Raw JSON</summary>"
        f"<pre>{_esc(json.dumps(obj, indent=2)[:20000])}</pre>"
        "</details>"
    )
    return header + agents_panel + final_panel + raw


def _require_fastapi():
    try:
        from fastapi import FastAPI, File, Form, UploadFile, Request
        from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("FastAPI dependencies missing; run: pip install -r requirements.txt") from exc
    return FastAPI, File, Form, UploadFile, HTMLResponse, Request, RedirectResponse, JSONResponse


FastAPI, File, Form, UploadFile, HTMLResponse, Request, RedirectResponse, JSONResponse = _require_fastapi()

from contextlib import asynccontextmanager

@asynccontextmanager
async def _lifespan(app):
    # â”€â”€ Startup â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        from workflows.trigger_runtime import get_runtime
        _rt = get_runtime()
        _rt.start()
        logger.info("TriggerRuntime started")
    except Exception as _e:
        logger.warning("TriggerRuntime start failed: %s", _e)
    yield
    # â”€â”€ Shutdown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        from workflows.trigger_runtime import get_runtime
        get_runtime().stop()
    except Exception:
        pass

app = FastAPI(title="Assignment Intelligence Platform", version="0.1", lifespan=_lifespan)


# â”€â”€ CORS â€” allows the React frontend (Vercel) to call this API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
try:
    from fastapi.middleware.cors import CORSMiddleware
    _frontend_origins = [
        "http://localhost:5173",                        # Vite dev
        "http://localhost:3000",                        # alternate dev
        "https://evaluator-engine.vercel.app",          # Vercel production
        "https://evaluator-engine-web.onrender.com",    # Render (same-origin calls)
    ]
    # Also allow any *.vercel.app preview deploy (covers all Vercel preview URLs)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_frontend_origins,
        allow_origin_regex=r"https://.*\.vercel\.app",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
except Exception:
    pass

templates = None
try:  # Optional: will be available after `pip install -r requirements.txt`
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(directory="templates")
except Exception:
    templates = None


@app.post("/presence/ping")
async def presence_ping(payload: dict):
    page = str(payload.get("page") or "global")
    cid = str(payload.get("client_id") or "")
    if not cid:
        cid = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    PRESENCE.setdefault(page, {})[cid] = now_ms
    _presence_cleanup(now_ms)
    return {"ok": True, "page": page, "count": len(PRESENCE.get(page, {}))}


@app.get("/login", response_class=HTMLResponse)
def login(request: Request, mode: str = "", error: str = ""):
    # If already logged in, don't show login page (avoids confusion).
    user = _current_user(request)
    if user:
        role = str(user.get("role") or "").lower()
        if role == "admin":
            return RedirectResponse(url="/admin", status_code=303)
        if role in {"instructor"}:
            return RedirectResponse(url="/instructor/assignments", status_code=303)
        return RedirectResponse(url="/me", status_code=303)

    demo = (os.environ.get("DEMO_AUTH", "") or "").strip() == "1"
    m = (mode or "").strip().lower()

    if demo and not m:
        if templates is not None:
            return templates.TemplateResponse(
                "pages/portal.html",
                {"request": request, "title": "Portal", "user": None},
            )
        return _render_raw(request, "Portal", "<p>Templates unavailable.</p>")

    if demo and m == "student":
        if templates is not None:
            return templates.TemplateResponse(
                "pages/login.html",
                {
                    "request": request,
                    "title": "Student Portal",
                    "subtitle": "Enter any username. A student account is created automatically.",
                    "user": None,
                    "action": "/auth/demo/student",
                    "fields": [{"name": "username", "label": "Username", "placeholder": "student1", "type": "text", "required": True}],
                    "button_text": "Enter as Student",
                    "show_back": True,
                    "footer_html": "",
                    "error": error or "",
                },
            )
        return _render_raw(request, "Student Portal", "<p>Templates unavailable.</p>")

    if demo and m == "instructor":
        if templates is not None:
            return templates.TemplateResponse(
                "pages/login.html",
                {
                    "request": request,
                    "title": "Instructor Portal",
                    "subtitle": "Use the shared instructor password.",
                    "user": None,
                    "action": "/auth/demo/instructor",
                    "fields": [{"name": "password", "label": "Shared Password", "placeholder": "", "type": "password", "required": True}],
                    "button_text": "Enter as Instructor",
                    "show_back": True,
                    "footer_html": "",
                    "error": error or "",
                },
            )
        return _render_raw(request, "Instructor Portal", "<p>Templates unavailable.</p>")

    # Normal (secure) login mode.
    if templates is not None:
        return templates.TemplateResponse(
            "pages/login.html",
            {
                "request": request,
                "title": "Login",
                "subtitle": "Use your account credentials.",
                "user": None,
                "action": "/auth/login",
                "fields": [
                    {"name": "identity", "label": "Username or Email", "placeholder": "alice", "type": "text", "required": True},
                    {"name": "password", "label": "Password", "placeholder": "", "type": "password", "required": True},
                ],
                "button_text": "Login",
                "show_back": False,
                "footer_html": "<a class='text-sky-600 hover:underline dark:text-sky-400' href='/register'>Create account</a>",
                "error": error or "",
            },
        )
    return _render_raw(request, "Login", "<p>Templates unavailable.</p>")


@app.get("/register", response_class=HTMLResponse)
def register(request: Request, error: str = ""):
    if (os.environ.get("DEMO_AUTH", "") or "").strip() == "1":
        return RedirectResponse(url="/login", status_code=303)
    if templates is not None:
        return templates.TemplateResponse(
            "pages/login.html",
            {
                "request": request,
                "title": "Register",
                "subtitle": "Create a new account.",
                "user": None,
                "action": "/auth/register",
                "fields": [
                    {"name": "username", "label": "Username", "placeholder": "alice", "type": "text", "required": True},
                    {"name": "email", "label": "Email", "placeholder": "alice@example.com", "type": "text", "required": True},
                    {"name": "phone", "label": "Phone (optional)", "placeholder": "+91...", "type": "text", "required": False},
                    {"name": "password", "label": "Password (min 8 chars)", "placeholder": "", "type": "password", "required": True},
                ],
                "button_text": "Create Account",
                "show_back": False,
                "footer_html": "<a class='text-sky-600 hover:underline dark:text-sky-400' href='/login'>Back to login</a>",
                "error": error or "",
            },
        )
    return _render_raw(request, "Register", "<p>Templates unavailable.</p>")


@app.post("/auth/register", response_class=HTMLResponse)
def auth_register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
):
    from urllib.parse import quote

    from assignment_intel.auth import hash_password, issue_session_token
    from assignment_intel.db import count_users, create_user, get_user_by_email, get_user_by_username

    u = (username or "").strip().lower()
    e = (email or "").strip().lower()
    if not u or not e:
        return RedirectResponse(url=f"/register?error={quote('Invalid username/email.')}", status_code=303)
    if get_user_by_username(u) or get_user_by_email(e):
        return RedirectResponse(url=f"/register?error={quote('User already exists.')}", status_code=303)

    role = "student"
    if count_users() == 0:
        role = "admin"
    elif (os.environ.get("ALLOW_INSTRUCTOR_SIGNUP", "") or "").strip() == "1" and u.endswith("_instructor"):
        role = "instructor"

    try:
        pw_hash = hash_password(password)
    except Exception as exc:
        return RedirectResponse(url=f"/register?error={quote('Invalid password: ' + str(exc))}", status_code=303)

    uid = create_user(username=u, email=e, password_hash=pw_hash, phone=phone.strip() or None, role=role)
    token = issue_session_token(user_id=uid, username=u, role=role)
    resp = RedirectResponse(url="/assignments", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.post("/auth/login", response_class=HTMLResponse)
def auth_login(request: Request, identity: str = Form(...), password: str = Form(...)):
    if (os.environ.get("DEMO_AUTH", "") or "").strip() == "1":
        return RedirectResponse(url="/login", status_code=303)
    from urllib.parse import quote
    from assignment_intel.auth import issue_session_token, verify_password
    from assignment_intel.db import get_user_by_email, get_user_by_username

    ident = (identity or "").strip().lower()
    user = get_user_by_username(ident) or get_user_by_email(ident)
    if not user or not verify_password(password, str(user.get("password_hash") or "")):
        return RedirectResponse(url=f"/login?error={quote('Invalid credentials.')}", status_code=303)

    token = issue_session_token(user_id=int(user["id"]), username=str(user["username"]), role=str(user["role"]))
    resp = RedirectResponse(url="/assignments", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.post("/auth/logout", response_class=HTMLResponse)
def auth_logout(request: Request):
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.post("/auth/demo/student", response_class=HTMLResponse)
def auth_demo_student(request: Request, username: str = Form(...)):
    if (os.environ.get("DEMO_AUTH", "") or "").strip() != "1":
        return RedirectResponse(url="/login", status_code=303)
    from urllib.parse import quote

    from assignment_intel.auth import issue_session_token
    from assignment_intel.db import create_user, get_user_by_username
    from assignment_intel.storage import safe_slug

    u = safe_slug(username or "")
    existing = get_user_by_username(u)
    if existing:
        if str(existing.get("role") or "").lower() != "student":
            return RedirectResponse(url=f"/login?mode=student&error={quote('This username is reserved.')}", status_code=303)
        token = issue_session_token(user_id=int(existing["id"]), username=str(existing["username"]), role=str(existing["role"]))
        resp = RedirectResponse(url="/me", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax")
        return resp

    # Create a student account on the fly (passwordless demo).
    email = f"{u}@demo.local"
    uid = create_user(username=u, email=email, password_hash="demo", phone=None, role="student")
    token = issue_session_token(user_id=uid, username=u, role="student")
    resp = RedirectResponse(url="/me", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.post("/auth/demo/instructor", response_class=HTMLResponse)
def auth_demo_instructor(request: Request, password: str = Form(...)):
    if (os.environ.get("DEMO_AUTH", "") or "").strip() != "1":
        return RedirectResponse(url="/login", status_code=303)
    from urllib.parse import quote

    shared = (os.environ.get("INSTRUCTOR_PASSWORD", "") or "").strip()
    if not shared:
        return RedirectResponse(
            url=f"/login?mode=instructor&error={quote('Instructor password not configured. Set INSTRUCTOR_PASSWORD and restart.')}",
            status_code=303,
        )
    if (password or "") != shared:
        return RedirectResponse(url=f"/login?mode=instructor&error={quote('Invalid instructor password.')}", status_code=303)

    from assignment_intel.auth import issue_session_token
    from assignment_intel.db import create_user, get_user_by_username

    u = "instructor"
    existing = get_user_by_username(u)
    if not existing:
        uid = create_user(username=u, email="instructor@demo.local", password_hash="demo", phone=None, role="instructor")
        role = "instructor"
    else:
        uid = int(existing["id"])
        role = str(existing.get("role") or "instructor")
    token = issue_session_token(user_id=uid, username=u, role=role)
    resp = RedirectResponse(url="/instructor/assignments", status_code=303)
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.get("/", response_class=HTMLResponse)
def index(request: Request, problem_id: str = ""):
    user = _current_user(request)
    if user:
        role = str(user.get("role") or "").lower()
        if role == "admin":
            return RedirectResponse(url="/admin", status_code=303)
        if role == "instructor":
            return RedirectResponse(url="/instructor/assignments", status_code=303)
        return RedirectResponse(url="/assignments", status_code=303)

    # Not logged in: show a clean landing page.
    if templates is not None:
        return templates.TemplateResponse("pages/welcome.html", {"request": request, "title": "Welcome", "user": None})
    return _render_raw(request, "Welcome", "<p><a href='/login'>Login</a> or <a href='/register'>Register</a></p>")


@app.get("/legacy-submit", response_class=HTMLResponse)
def legacy_submit(request: Request, problem_id: str = ""):
    """Legacy file-upload submit page (kept for backwards compatibility)."""
    user = _current_user(request)
    pid_val = _esc(problem_id) if problem_id else ""
    if not user:
        body = _nav(request) + _presence_widget("submit") + (
            "<div class='card'><p><strong>Login required</strong> to submit solutions.</p>"
            "<p><a href='/login'>Login</a> or <a href='/register'>Register</a></p></div>"
        )
        return _html_page("Submit Assignment", body)

    body = _nav(request) + _presence_widget("submit") + f"""
    <div class="card">
      <p><a href="/assignments"><strong>Browse assignments</strong></a></p>
      <form action="/submit" method="post" enctype="multipart/form-data">
        <label>Problem ID</label>
        <input name="problem_id" placeholder="add_numbers" value="{pid_val}" required />

        <label>Submission File (.py / .java / .c / .cpp / .js)</label>
        <input type="file" name="file" required />

        <button type="submit">Submit and Evaluate</button>
      </form>
    </div>
    <p>Tools: <a href="/reports">/reports</a> | <a href="/mcp/tools">/mcp/tools</a></p>
    """
    return _html_page("Submit Assignment", body)


@app.get("/assignments", response_class=HTMLResponse)
def assignments(request: Request):
    from assignment_intel.db import list_active_assignments, list_student_assignments

    user = _current_user(request)
    role = str(user.get("role") or "").lower() if isinstance(user, dict) else ""
    if role in {"student", "instructor", "admin"}:
        # Students should see assigned problems even while generation is pending/failed.
        rows = list_student_assignments()
    else:
        # Public view (not logged in): only active problems.
        rows = list_active_assignments()
    if templates is not None:
        return templates.TemplateResponse(
            "pages/assignments.html",
            {"request": request, "title": "Assignments", "user": user, "assignments": rows},
        )

    # Fallback (no Jinja installed yet)
    items = ""
    for a in rows:
        aid = str(a.get("id") or "")
        title = str(a.get("title") or "")
        vis = int(a.get("visible_tests") or 0)
        hid = int(a.get("hidden_tests") or 0)
        stress = int(a.get("stress_tests") or 0)
        items += (
            "<tr>"
            f"<td><code>{_esc(aid)}</code></td>"
            f"<td>{_esc(title)}</td>"
            f"<td><code>{vis}</code></td>"
            f"<td><code>{hid}</code></td>"
            f"<td><code>{stress}</code></td>"
            f"<td><a href='/assignment?id={_esc(aid)}'>view</a></td>"
            "</tr>"
        )
    html_body = (
        "<div class='card'>"
        "<p>Available assignments. Use the assignment id as <code>Problem ID</code> when submitting.</p>"
        "<table><thead><tr><th>ID</th><th>Title</th><th>Visible</th><th>Hidden</th><th>Stress</th><th></th></tr></thead>"
        f"<tbody>{items or '<tr><td colspan=6>No assignments yet</td></tr>'}</tbody></table>"
        "</div>"
        "<p><a href='/'>Back</a></p>"
    )
    return _render_raw(request, "Assignments", html_body)


@app.get("/assignment", response_class=HTMLResponse)
def assignment(request: Request, id: str):
    from assignment_intel.db import get_assignment, list_test_cases

    a = get_assignment(assignment_id=id)
    if not a:
        return _html_page("Not Found", "<p>Assignment not found.</p><p><a href='/assignments'>Back</a></p>")

    cases = list_test_cases(assignment_id=id)
    vis = [c for c in cases if str(c.get("visibility")) == "visible"]
    hid = [c for c in cases if str(c.get("visibility")) != "visible"]

    # Show only visible test inputs/expected; hidden/stress expected outputs are never shown.
    rows = ""
    for c in vis[:3]:
        rows += (
            "<tr>"
            f"<td>{_esc(c.get('id'))}</td>"
            f"<td><code>{_esc(c.get('visibility'))}</code></td>"
            f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(c.get('input_text'))}</pre></td>"
            f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(c.get('expected_output'))}</pre></td>"
            "</tr>"
        )
    if not rows:
        rows = "<tr><td colspan=4>No visible test cases published</td></tr>"

    page_key = f"assignment:{id}"
    # Generated metadata
    constraints = str(a.get("constraints_text") or "").strip()
    difficulty = str(a.get("difficulty") or "").strip()
    generated_desc = str(a.get("generated_description") or "").strip()
    input_format = str(a.get("input_format") or "").strip()
    output_format = str(a.get("output_format") or "").strip()
    tags_raw = a.get("tags_json")
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) and tags_raw.strip() else []
    except Exception:
        tags = []
    examples_raw = a.get("examples_json")
    try:
        examples = json.loads(examples_raw) if isinstance(examples_raw, str) and examples_raw.strip() else []
    except Exception:
        examples = []

    meta_block = ""
    if generated_desc or constraints or difficulty or tags or examples or input_format or output_format:
        ex_html = ""
        if isinstance(examples, list) and examples:
            for ex in examples[:3]:
                if not isinstance(ex, dict):
                    continue
                ex_html += (
                    "<div class='card' style='margin-top:10px'>"
                    f"<p><strong>Input:</strong></p><pre style='background:#fff; color:#111'>{_esc(ex.get('input'))}</pre>"
                    f"<p><strong>Output:</strong></p><pre style='background:#fff; color:#111'>{_esc(ex.get('output'))}</pre>"
                    f"<p><strong>Explanation:</strong> {_esc(ex.get('explanation') or '')}</p>"
                    "</div>"
                )
        meta_block = (
            "<div class='card' style='margin-top:12px'>"
            "<h3 style='margin-top:0'>Problem (Expanded)</h3>"
            f"<pre style='background:#fff; color:#111'>{_esc(generated_desc or (a.get('description') or ''))}</pre>"
            "<h3>Input Format</h3>"
            f"<pre style='background:#fff; color:#111'>{_esc(input_format)}</pre>"
            "<h3>Output Format</h3>"
            f"<pre style='background:#fff; color:#111'>{_esc(output_format)}</pre>"
            "<h3>Constraints</h3>"
            f"<pre style='background:#fff; color:#111'>{_esc(constraints)}</pre>"
            f"<p><strong>Difficulty:</strong> <code>{_esc(difficulty)}</code></p>"
            f"<p><strong>Tags:</strong> <code>{_esc(tags)}</code></p>"
            + ("<h3>Examples</h3>" + ex_html if ex_html else "")
            + "</div>"
        )

    # Parse metadata fields for template
    constraints = str(a.get("constraints_text") or "").strip()
    difficulty = str(a.get("difficulty") or "").strip()
    generated_desc = str(a.get("generated_description") or "").strip()
    input_format = str(a.get("input_format") or "").strip()
    output_format = str(a.get("output_format") or "").strip()
    tags_raw = a.get("tags_json")
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) and tags_raw.strip() else []
    except Exception:
        tags = []
    examples_raw = a.get("examples_json")
    try:
        examples = json.loads(examples_raw) if isinstance(examples_raw, str) and examples_raw.strip() else []
    except Exception:
        examples = []

    # Starter templates per language (IO problems)
    starters = {
        "python": "import sys\n\n\ndef main():\n    data = sys.stdin.read().strip().split()\n    # TODO: implement\n    print(\"\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
        "javascript": "const fs = require('fs');\n\nfunction main() {\n  const data = fs.readFileSync(0,'utf8').trim().split(/\\s+/);\n  // TODO: implement\n  process.stdout.write(\"\\n\");\n}\n\nmain();\n",
        "java": "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n  public static void main(String[] args) throws Exception {\n    String input = new String(System.in.readAllBytes());\n    String[] data = input.trim().isEmpty() ? new String[0] : input.trim().split(\"\\\\s+\");\n    // TODO: implement\n    System.out.println(\"\");\n  }\n}\n",
        "c": "#include <stdio.h>\n\nint main() {\n  // TODO: read stdin, solve, print stdout\n  return 0;\n}\n",
        "cpp": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n  ios::sync_with_stdio(false);\n  cin.tie(nullptr);\n  // TODO: read stdin, solve, print stdout\n  return 0;\n}\n",
    }
    user = _current_user(request)
    default_lang = "python"
    code = starters.get(default_lang, "")

    if templates is not None:
        # Convert test case dicts into template-friendly objects
        visible_tests = []
        for c in vis:
            visible_tests.append({"input_text": c.get("input_text", ""), "expected_output": c.get("expected_output", "")})
        ex_norm = []
        if isinstance(examples, list):
            for ex in examples:
                if isinstance(ex, dict):
                    ex_norm.append(
                        {
                            "input": str(ex.get("input") or ""),
                            "output": str(ex.get("output") or ""),
                            "explanation": str(ex.get("explanation") or ""),
                        }
                    )

        return templates.TemplateResponse(
            "pages/assignment.html",
            {
                "request": request,
                "title": str(a.get("title") or "Assignment"),
                "user": user,
                "assignment": {
                    "id": str(a.get("id") or ""),
                    "title": str(a.get("title") or ""),
                    "description": str(a.get("description") or ""),
                    "difficulty": difficulty,
                    "active": int(a.get("active") or 0) == 1,
                    "generation_status": str(a.get("generation_status") or ""),
                    "generation_error": str(a.get("generation_error") or ""),
                    "visible_tests": len(vis),
                },
                "constraints": constraints,
                "generated_description": generated_desc,
                "input_format": input_format,
                "output_format": output_format,
                "tags": tags if isinstance(tags, list) else [],
                "examples": ex_norm,
                "visible_tests": visible_tests,
                "language": default_lang,
                "code": code,
                "starters": starters,
            },
        )

    # Fallback to old body if templates missing.
    body = _nav(request) + _presence_widget(page_key) + meta_block
    return _html_page("Assignment", body)


@app.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, assignment_id: str):
    from assignment_intel.db import get_conn

    aid = (assignment_id or "").strip()
    if not aid:
        return _html_page("Leaderboard", _nav(request) + "<p>Missing assignment_id.</p>")

    # Best score per user; tie-breaker by earliest best submission.
    with get_conn() as conn:
        rows = conn.execute(
            """
            WITH ranked AS (
              SELECT s.username,
                     s.student_name,
                     s.language,
                     e.score,
                     e.created_at AS submitted_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY s.username
                       ORDER BY e.score DESC, e.created_at ASC
                     ) AS rn
                FROM evaluations e
                JOIN submissions s ON s.id = e.submission_id
               WHERE s.problem_id = ? AND e.status = 'completed'
            )
            SELECT username, student_name, language, score, submitted_at
              FROM ranked
             WHERE rn = 1
          ORDER BY score DESC, submitted_at ASC
             LIMIT 200
            """,
            (aid,),
        ).fetchall()

    out_rows = []
    rank = 1
    for r in rows:
        out_rows.append(
            {
                "rank": rank,
                "username": r["username"],
                "student_name": r["student_name"],
                "score": r["score"],
                "language": r["language"],
                "submitted_at": r["submitted_at"],
            }
        )
        rank += 1

    if templates is not None:
        return templates.TemplateResponse(
            "pages/leaderboard.html",
            {"request": request, "title": "Leaderboard", "user": _current_user(request), "assignment_id": aid, "rows": out_rows},
        )

    # Fallback
    items = ""
    for row in out_rows:
        items += (
            "<tr>"
            f"<td><code>{row['rank']}</code></td>"
            f"<td><code>{_esc(row['username'])}</code></td>"
            f"<td>{_esc(row['student_name'])}</td>"
            f"<td><code>{_esc(row['score'])}</code></td>"
            f"<td><code>{_esc(row['language'])}</code></td>"
            f"<td><code>{_esc(row['submitted_at'])}</code></td>"
            "</tr>"
        )
    if not items:
        items = "<tr><td colspan=6>No completed submissions yet</td></tr>"
    body = (
        "<div class='card'>"
        f"<p><strong>Assignment:</strong> <code>{_esc(aid)}</code></p>"
        "<table><thead><tr><th>Rank</th><th>Username</th><th>Name</th><th>Score</th><th>Lang</th><th>Submission Time</th></tr></thead>"
        f"<tbody>{items}</tbody></table>"
        "</div>"
        + f"<p><a href='/assignment?id={_esc(aid)}'>Back to problem</a></p>"
    )
    return _render_raw(request, "Leaderboard", body)


@app.get("/leaderboards", response_class=HTMLResponse)
def leaderboards(request: Request):
    """Leaderboard index page (picks an assignment_id)."""
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    from assignment_intel.db import list_student_assignments

    rows = list_student_assignments()
    if templates is not None:
        return templates.TemplateResponse(
            "pages/leaderboards.html",
            {"request": request, "title": "Leaderboards", "user": _current_user(request), "assignments": rows},
        )
    items = "".join([f"<li><a href='/leaderboard?assignment_id={_esc(r.get('id'))}'>{_esc(r.get('title'))}</a></li>" for r in rows])
    return _render_raw(request, "Leaderboards", f"<div class='card'><ul>{items}</ul></div>")


@app.get("/instructor/assignments", response_class=HTMLResponse)
def instructor_assignments(request: Request):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import list_assignments

    rows = list_assignments()

    # Daily auto-assign status (no schema changes; stored in a small state JSON file).
    from pathlib import Path as _Path
    import json as _json
    st_path = _Path(os.getenv("DAILY_ASSIGN_STATE_FILE", "results/auto_assign_state.json"))
    try:
        st = _json.loads(st_path.read_text(encoding="utf-8")) if st_path.exists() else {}
    except Exception:
        st = {}
    daily = {
        "enabled": os.getenv("DAILY_ASSIGN_ENABLED", "0").strip().lower() in {"1","true","yes","on"},
        "at_utc": os.getenv("DAILY_ASSIGN_AT_UTC", "07:00"),
        "sources": os.getenv("DAILY_ASSIGN_SOURCES", "leetcode,geeksforgeeks,neetcode,hackerearth"),
        "last_date": str(st.get("last_date") or ""),
        "rotation_index": int(st.get("rotation_index") or 0),
    }

    if templates is not None:
        norm = []
        for a in rows:
            norm.append(
                {
                    "id": str(a.get("id") or ""),
                    "title": str(a.get("title") or ""),
                    "generation_status": str(a.get("generation_status") or ""),
                    "generation_error": str(a.get("generation_error") or ""),
                    "active": int(a.get("active") or 0) == 1,
                    "archived": int(a.get("archived") or 0) == 1,
                    "visible_tests": int(a.get("visible_tests") or 0),
                    "hidden_tests": int(a.get("hidden_tests") or 0),
                }
            )
        return templates.TemplateResponse(
            "pages/instructor_assignments.html",
            {"request": request, "title": "Instructor Panel", "user": _current_user(request), "assignments": norm, "daily": daily}
        )

    return _html_page("Instructor: Assignments", _nav(request) + "<p>Templates unavailable.</p>")


@app.get("/instructor/analytics", response_class=HTMLResponse)
def instructor_analytics(request: Request):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard

    from assignment_intel.db import get_conn

    with get_conn() as conn:
        totals = conn.execute(
            """
            SELECT COUNT(1) AS total_submissions,
                   COUNT(DISTINCT username) AS unique_students
              FROM submissions
            """
        ).fetchone()
        avg = conn.execute(
            "SELECT AVG(score) AS avg_score FROM evaluations WHERE status='completed'"
        ).fetchone()
        langs = conn.execute(
            """
            SELECT LOWER(language) AS lang, COUNT(1) AS c
              FROM submissions
          GROUP BY LOWER(language)
          ORDER BY c DESC
            """
        ).fetchall()
        recent = conn.execute(
            """
            SELECT e.id AS evaluation_id, e.status, e.score, e.report_path, e.created_at,
                   s.id AS submission_id,
                   s.username, s.student_name, s.phone, s.problem_id, s.language
              FROM evaluations e
              JOIN submissions s ON s.id = e.submission_id
          ORDER BY e.id DESC
             LIMIT 50
            """
        ).fetchall()
        by_day = conn.execute(
            """
            SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(1) AS c
              FROM submissions
          GROUP BY SUBSTR(created_at, 1, 10)
          ORDER BY day DESC
             LIMIT 14
            """
        ).fetchall()

    lang_counts = {r["lang"]: int(r["c"]) for r in langs} if langs else {}
    total_subs = int(totals["total_submissions"] or 0) if totals else 0
    uniq = int(totals["unique_students"] or 0) if totals else 0
    avg_score = round(float(avg["avg_score"] or 0.0), 2) if avg else 0.0

    if templates is not None:
        days = list(reversed([str(r["day"]) for r in by_day])) if by_day else []
        counts = list(reversed([int(r["c"]) for r in by_day])) if by_day else []
        recent_norm = []
        for r in recent:
            rp = r["report_path"]
            recent_norm.append(
                {
                    "evaluation_id": r["evaluation_id"],
                    "submission_id": r["submission_id"],
                    "username": r["username"],
                    "problem_id": r["problem_id"],
                    "language": r["language"],
                    "score": r["score"],
                    "report_path": Path(str(rp)).as_posix() if rp else None,
                }
            )
        return templates.TemplateResponse(
            "pages/instructor_analytics.html",
            {
                "request": request,
                "title": "Analytics",
                "user": _current_user(request),
                "metrics": {"total_submissions": total_subs, "unique_students": uniq, "avg_score": avg_score, "language_usage": lang_counts},
                "submission_rate": {"labels": days, "counts": counts},
                "recent": recent_norm,
            },
        )

    metrics = (
        "<div class='row'>"
        + _kpi("Total submissions", str(total_subs))
        + _kpi("Unique students", str(uniq))
        + _kpi("Average score", str(avg_score))
        + _kpi("Language dist", _esc(lang_counts))
        + "</div>"
    )
    return _html_page("Instructor Analytics", _nav(request) + metrics)


@app.post("/instructor/assignments/save", response_class=HTMLResponse)
async def instructor_assignments_save(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
):
    from assignment_intel.db import upsert_assignment
    from assignment_intel.db import get_assignment
    from assignment_intel.db import enqueue_job, set_assignment_generation_status

    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard

    base = safe_slug(title)
    aid = base
    # Ensure id uniqueness.
    if get_assignment(assignment_id=aid):
        import time as _time

        aid = f"{base}_{int(_time.time())}"
    upsert_assignment(assignment_id=aid, title=title.strip(), description=description.strip())

    # Queue AI generation job (worker executes it). If AI isn't configured, avoid queuing a job that will fail.
    if os.getenv("AI_PROVIDER", "null").strip().lower() != "openai" or not os.getenv("OPENAI_API_KEY", "").strip():
        set_assignment_generation_status(assignment_id=aid, status="failed", error="openai_not_configured", active=False)
        return RedirectResponse(url=f"/instructor/assignment?id={_esc(aid)}", status_code=303)

    set_assignment_generation_status(assignment_id=aid, status="queued", error=None, active=False)
    job_id = enqueue_job(job_type="problem_generation", payload={"assignment_id": aid})
    return RedirectResponse(
        url=f"/instructor/assignments?toast=Created%20{_esc(aid)}%20and%20queued%20AI%20generation%20(job%20{_esc(job_id)}).",
        status_code=303,
    )


@app.post("/instructor/daily/run", response_class=HTMLResponse)
def instructor_daily_run(request: Request):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    try:
        from assignment_intel.daily_auto_assign import maybe_create_daily_assignment

        res = maybe_create_daily_assignment(force=True)
        if not res:
            return RedirectResponse(url="/instructor/assignments?toast=Daily%20auto-assign%20is%20disabled%20(DAILY_ASSIGN_ENABLED=1).", status_code=303)
        msg = f"Created%20{_esc(res.assignment_id)}%20from%20{_esc(res.source)}%20and%20queued%20job%20{_esc(res.job_id)}." if int(res.job_id or 0) > 0 else f"Created%20{_esc(res.assignment_id)}%20but%20AI%20not%20configured."
        return RedirectResponse(url=f"/instructor/assignments?toast={msg}", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/instructor/assignments?toast=Daily%20auto-assign%20failed:%20{_esc(str(exc))}", status_code=303)

@app.get("/instructor/assignment", response_class=HTMLResponse)
def instructor_assignment(request: Request, id: str):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import get_assignment, list_test_cases, list_recent_evaluations_for_problem

    a = get_assignment(assignment_id=id)
    if not a:
        return _html_page("Not Found", "<p>Assignment not found.</p><p><a href='/instructor/assignments'>Back</a></p>")

    cases = list_test_cases(assignment_id=id)
    evals = list_recent_evaluations_for_problem(id, limit=300)

    unique_users = len({str(r.get("username") or "") for r in evals})
    total_attempts = len(evals)
    completed = [r for r in evals if str(r.get("status")) == "completed"]
    avg_score = round(sum(float(r.get("score") or 0.0) for r in completed) / len(completed), 2) if completed else 0.0
    # A "solve" is an evaluation with score >= 60 (tune later).
    solved = sum(1 for r in completed if float(r.get("score") or 0.0) >= 60.0)

    gen_status = str(a.get("generation_status") or "").strip() or "unknown"
    gen_error = str(a.get("generation_error") or "").strip()
    active = int(a.get("active") or 0)
    stats = (
        _nav(request)
        + _presence_widget(f"assignment:{id}")
        + "<div class='card'>"
        + f"<p><strong>Assignment:</strong> <code>{_esc(id)}</code></p>"
        + f"<p><strong>Generation:</strong> <code>{_esc(gen_status)}</code> &nbsp; "
        + f"<strong>Status:</strong> {_badge('ACTIVE' if active else 'INACTIVE', 'ok' if active else 'warn')}</p>"
        + (f"<p><strong>Generation error:</strong> <code>{_esc(gen_error)}</code></p>" if gen_error else "")
        + f"<p><strong>Unique students:</strong> <code>{unique_users}</code> &nbsp; "
        + f"<strong>Total submissions:</strong> <code>{total_attempts}</code> &nbsp; "
        + f"<strong>Solved (>=60):</strong> <code>{solved}</code></p>"
        + f"<p><strong>Average score:</strong> <code>{avg_score}</code></p>"
        + f"<p><a href='/leaderboard?assignment_id={_esc(id)}'>View leaderboard</a></p>"
        + "</div>"
    )
    rows = ""
    for c in cases:
        cid = int(c.get("id") or 0)
        rows += (
            "<tr>"
            f"<td><code>{_esc(cid)}</code></td>"
            f"<td><code>{_esc(c.get('visibility'))}</code></td>"
            f"<td><code>{_esc(c.get('weight'))}</code></td>"
            f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(c.get('input_text'))}</pre></td>"
            f"<td><pre style='margin:0; background:#fff; color:#111'>{_esc(c.get('expected_output'))}</pre></td>"
            f"<td><form action='/instructor/testcase/delete' method='post' style='margin:0'>"
            f"<input type='hidden' name='assignment_id' value='{_esc(id)}'/>"
            f"<input type='hidden' name='test_case_id' value='{_esc(cid)}'/>"
            "<button type='submit'>Delete</button></form></td>"
            "</tr>"
        )
    if not rows:
        rows = "<tr><td colspan=6>No test cases yet</td></tr>"

    # Recent evaluation table for this assignment
    eval_rows = ""
    for r in evals[:200]:
        rp = r.get("report_path")
        link = f"<a href='/report?path={Path(str(rp)).as_posix()}'>report</a>" if rp else "-"
        eval_rows += (
            "<tr>"
            f"<td><code>{_esc(r.get('evaluation_id'))}</code></td>"
            f"<td><code>{_esc(r.get('username'))}</code></td>"
            f"<td>{_esc(r.get('student_name'))}</td>"
            f"<td><code>{_esc(r.get('phone') or '')}</code></td>"
            f"<td><code>{_esc(r.get('language'))}</code></td>"
            f"<td><code>{_esc(r.get('status'))}</code></td>"
            f"<td><code>{_esc(r.get('score'))}</code></td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    if not eval_rows:
        eval_rows = "<tr><td colspan=8>No submissions yet</td></tr>"

    if templates is not None:
        norm_cases = []
        for c in cases:
            norm_cases.append(
                {
                    "id": int(c.get("id") or 0),
                    "visibility": str(c.get("visibility") or ""),
                    "weight": float(c.get("weight") or 1.0),
                    "input_text": str(c.get("input_text") or ""),
                    "expected_output": str(c.get("expected_output") or ""),
                }
            )
        norm_recent = []
        for r in evals[:200]:
            rp = r.get("report_path")
            norm_recent.append(
                {
                    "evaluation_id": r.get("evaluation_id"),
                    "submission_id": r.get("submission_id"),
                    "username": str(r.get("username") or ""),
                    "student_name": str(r.get("student_name") or ""),
                    "phone": str(r.get("phone") or "") if r.get("phone") else None,
                    "language": str(r.get("language") or ""),
                    "status": str(r.get("status") or ""),
                    "score": float(r.get("score") or 0.0),
                    "report_path": Path(str(rp)).as_posix() if rp else None,
                }
            )
        return templates.TemplateResponse(
            "pages/instructor_assignment.html",
            {
                "request": request,
                "title": "Manage Assignment",
                "user": _current_user(request),
                "assignment": {
                    "id": str(a.get("id") or ""),
                    "title": str(a.get("title") or ""),
                    "generation_status": str(a.get("generation_status") or ""),
                    "generation_error": str(a.get("generation_error") or ""),
                    "active": int(a.get("active") or 0) == 1,
                    "archived": int(a.get("archived") or 0) == 1,
                },
                "stats": {"unique_users": unique_users, "total_attempts": total_attempts, "solved": solved, "avg_score": avg_score},
                "test_cases": norm_cases,
                "recent": norm_recent,
            },
        )

    return _html_page("Instructor: Manage Assignment", stats)


@app.post("/instructor/assignment/retry", response_class=HTMLResponse)
async def instructor_assignment_retry(request: Request, assignment_id: str = Form(...)):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import enqueue_job, set_assignment_generation_status

    aid = (assignment_id or "").strip()
    if not aid:
        return RedirectResponse(url="/instructor/assignments", status_code=303)
    set_assignment_generation_status(assignment_id=aid, status="queued", error=None, active=False)
    enqueue_job(job_type="problem_generation", payload={"assignment_id": aid})
    return RedirectResponse(url=f"/instructor/assignment?id={_esc(aid)}", status_code=303)


@app.post("/instructor/assignment/publish", response_class=HTMLResponse)
async def instructor_assignment_publish(request: Request, assignment_id: str = Form(...)):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import list_test_cases, set_assignment_active, set_assignment_archived

    aid = (assignment_id or "").strip()
    if not aid:
        return RedirectResponse(url="/instructor/assignments", status_code=303)
    cases = list_test_cases(assignment_id=aid)
    visible_n = sum(1 for c in cases if str(c.get("visibility") or "").lower() == "visible")
    if visible_n < 3:
        return RedirectResponse(url=f"/instructor/assignment?id={_esc(aid)}", status_code=303)
    set_assignment_archived(assignment_id=aid, archived=False)
    set_assignment_active(assignment_id=aid, active=True)
    return RedirectResponse(url=f"/instructor/assignment?id={_esc(aid)}", status_code=303)


@app.post("/instructor/assignment/archive", response_class=HTMLResponse)
async def instructor_assignment_archive(request: Request, assignment_id: str = Form(...)):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import set_assignment_archived

    aid = (assignment_id or "").strip()
    if aid:
        set_assignment_archived(assignment_id=aid, archived=True)
    return RedirectResponse(url="/instructor/assignments", status_code=303)


@app.post("/instructor/assignment/restore", response_class=HTMLResponse)
async def instructor_assignment_restore(request: Request, assignment_id: str = Form(...)):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import set_assignment_archived

    aid = (assignment_id or "").strip()
    if aid:
        set_assignment_archived(assignment_id=aid, archived=False)
    return RedirectResponse(url=f"/instructor/assignment?id={_esc(aid)}", status_code=303)


@app.get("/instructor/submission", response_class=HTMLResponse)
def instructor_submission(request: Request, submission_id: str):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import get_submission

    try:
        sid = int(submission_id)
    except ValueError:
        sid = 0
    if not sid:
        return RedirectResponse(url="/instructor/assignments", status_code=303)
    sub = get_submission(sid)
    if not sub:
        return _render_raw(request, "Not Found", "<p>Submission not found.</p><p><a href='/instructor/assignments'>Back</a></p>")

    path = Path(str(sub.get("submission_path") or "")).resolve()
    repo_root = Path(__file__).resolve().parent
    try:
        rel = path.relative_to(repo_root)
    except Exception:
        return _render_raw(request, "Blocked", "<p>Submission path is outside repo.</p>")
    if not rel.parts or rel.parts[0].lower() != "submissions":
        return _render_raw(request, "Blocked", "<p>Can only view submissions stored under <code>submissions/</code>.</p>")
    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _render_raw(request, "Error", f"<p>Could not read file: <code>{_esc(exc)}</code></p>")

    if templates is not None:
        return templates.TemplateResponse(
            "pages/instructor_submission.html",
            {
                "request": request,
                "title": "Submission",
                "user": _current_user(request),
                "submission": {
                    "id": int(sub.get("id") or 0),
                    "username": str(sub.get("username") or ""),
                    "student_name": str(sub.get("student_name") or ""),
                    "phone": str(sub.get("phone") or "") if sub.get("phone") else "",
                    "problem_id": str(sub.get("problem_id") or ""),
                    "language": str(sub.get("language") or ""),
                    "created_at": str(sub.get("created_at") or ""),
                    "path": str(rel).replace("\\", "/"),
                },
                "code": code,
            },
        )
    body = _nav(request) + f"<p><code>{_esc(rel)}</code></p><pre>{_esc(code)}</pre>"
    return _html_page("Submission", body)


@app.post("/instructor/testcase/add", response_class=HTMLResponse)
async def instructor_testcase_add(
    request: Request,
    assignment_id: str = Form(...),
    visibility: str = Form("visible"),
    weight: str = Form("1.0"),
    input_text: str = Form(...),
    expected_output: str = Form(...),
):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import add_test_case

    try:
        w = float(weight)
    except ValueError:
        w = 1.0
    add_test_case(
        assignment_id=assignment_id.strip(),
        input_text=input_text,
        expected_output=expected_output,
        visibility=visibility.strip().lower(),
        weight=w,
    )
    return _html_page("Added", f"<div class='card'><p>Test case added.</p><p><a href='/instructor/assignment?id={_esc(assignment_id)}'>Back</a></p></div>")


@app.post("/instructor/testcase/delete", response_class=HTMLResponse)
async def instructor_testcase_delete(
    request: Request,
    assignment_id: str = Form(...),
    test_case_id: str = Form(...),
):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import delete_test_case

    try:
        tid = int(test_case_id)
    except ValueError:
        tid = 0
    if tid:
        delete_test_case(test_case_id=tid)
    return _html_page("Deleted", f"<div class='card'><p>Test case deleted.</p><p><a href='/instructor/assignment?id={_esc(assignment_id)}'>Back</a></p></div>")


@app.post("/submit", response_class=HTMLResponse)
async def submit(
    request: Request,
    problem_id: str = Form(...),
    file: UploadFile = File(...),
):
    # Only students can submit solutions from the student portal.
    user = _require_role(request, {"student", "admin"})
    if isinstance(user, RedirectResponse):
        return user

    username = str(user.get("username") or "")
    phone = str(user.get("phone") or "") if user.get("phone") else ""
    try:
        content = await file.read()
        stored = save_submission_bytes(
            student_name=username,
            problem_id=problem_id,
            filename=file.filename or "submission.py",
            content=content,
        )
    except Exception as exc:
        return _html_page("Submission Error", _nav(request) + f"<p><code>{_esc(exc)}</code></p><p><a href='/'>Back</a></p>")

    if stored.language not in {"python", "java", "javascript", "cpp", "c"}:
        return _html_page(
            "Language Not Yet Supported",
            "<p>Supported: <code>.py</code>, <code>.java</code>, <code>.c</code>, <code>.cpp</code>, <code>.js</code>.</p><p><a href='/'>Back</a></p>",
        )

    from assignment_intel.eval_service import enqueue_only
    from assignment_intel.db import enqueue_job

    evaluation_id = enqueue_only(stored=stored, student_name=username, phone=phone.strip() or None)
    job_id = enqueue_job(job_type="solution_evaluation", payload={"evaluation_id": evaluation_id})

    body = (
        _nav(request)
        + "<div class='card'>"
        "<p>Submission saved and evaluation queued.</p>"
        f"<p>Saved submission: <code>{_esc(stored.path)}</code></p>"
        f"<p>Evaluation ID: <code>{_esc(evaluation_id)}</code></p>"
        f"<p>Job ID: <code>{_esc(job_id)}</code></p>"
        f"<p><a href='/evaluation?id={_esc(evaluation_id)}'>View evaluation status</a></p>"
        f"<p>Edit & Resubmit: <a href='/editor?problem_id={_esc(stored.problem_id)}'>open editor</a></p>"
        "</div>"
        "<p><a href='/'>Back</a></p>"
    )
    return _html_page("Queued", body)


@app.get("/evaluation", response_class=HTMLResponse)
def evaluation_status(request: Request, id: str):
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user

    from assignment_intel.db import get_evaluation, get_submission

    try:
        eid = int(id)
    except ValueError:
        eid = 0
    ev = get_evaluation(eid) if eid else None
    if not ev:
        return _html_page("Not Found", _nav(request) + "<p>Evaluation not found.</p><p><a href='/me'>Back</a></p>")

    sub = get_submission(ev.submission_id)
    if not sub:
        return _html_page("Not Found", _nav(request) + "<p>Submission not found.</p><p><a href='/me'>Back</a></p>")

    role = str(user.get("role") or "").lower()
    if role == "student" and str(sub.get("username") or "") != str(user.get("username") or ""):
        return RedirectResponse(url="/me", status_code=303)

    report_path = Path(ev.report_path) if ev.report_path else None
    report_href = None
    if report_path and report_path.exists():
        report_href = f"/report?path={report_path.as_posix()}"

    auto_refresh = ev.status in {"queued", "running"}

    # Stepper states
    status = str(ev.status)
    step_names = ["Queued", "Running tests", "Evaluating results", "Generating feedback", "Completed"]
    if status == "queued":
        idx = 0
    elif status == "running":
        idx = 1
    elif status == "completed":
        idx = 4
    elif status == "failed":
        idx = 2
    else:
        idx = 0

    steps = []
    for i, name in enumerate(step_names):
        if i < idx:
            state = "done"
        elif i == idx:
            state = "now"
        else:
            state = "todo"
        steps.append({"name": name, "state": state})

    progress = int(round((idx / (len(step_names) - 1)) * 100)) if len(step_names) > 1 else 0
    step_label = step_names[idx] if 0 <= idx < len(step_names) else status

    if templates is not None:
        return templates.TemplateResponse(
            "pages/evaluation.html",
            {
                "request": request,
                "title": "Evaluation Status",
                "user": _current_user(request),
                "evaluation": {"id": ev.id, "status": status, "score": ev.score, "error": ev.error},
                "submission": {"problem_id": str(sub.get("problem_id") or ""), "language": str(sub.get("language") or "")},
                "auto_refresh": auto_refresh,
                "steps": steps,
                "progress": progress,
                "step_label": step_label,
                "report_link": report_href,
            },
        )

    # Fallback
    return _html_page("Evaluation Status", _nav(request) + f"<p>Status: {status}</p>")


@app.get("/job", response_class=HTMLResponse)
def job_status(request: Request, id: str):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.db import get_job

    try:
        jid = int(id)
    except ValueError:
        jid = 0
    job = get_job(jid) if jid else None
    if not job:
        return _html_page("Not Found", _nav(request) + "<p>Job not found.</p>")

    refresh = ""
    if str(job.get("status")) in {"queued", "running"}:
        refresh = "<meta http-equiv='refresh' content='3'/>"

    body = (
        refresh
        + _nav(request)
        + "<div class='card'>"
        f"<p><strong>Job:</strong> <code>{_esc(job.get('id'))}</code></p>"
        f"<p><strong>Type:</strong> <code>{_esc(job.get('type'))}</code></p>"
        f"<p><strong>Status:</strong> <code>{_esc(job.get('status'))}</code></p>"
        + (f"<p><strong>Error:</strong> <code>{_esc(job.get('error'))}</code></p>" if job.get("error") else "")
        + "</div>"
        + "<details style='margin-top:12px' open><summary>Payload</summary>"
        + f"<pre>{_esc(json.dumps(job.get('payload') or {}, indent=2)[:8000])}</pre></details>"
        + "<details style='margin-top:12px'><summary>Result</summary>"
        + f"<pre>{_esc(json.dumps(job.get('result') or {}, indent=2)[:12000])}</pre></details>"
        + "<p><a href='/instructor/assignments'>Back</a></p>"
    )
    return _html_page("Job Status", body)

@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    guard = _require_role(request, {"admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    rows = list_recent_evaluations(limit=100)
    # Metrics
    completed = [r for r in rows if str(r.get("status")) == "completed"]
    avg_score = round(sum(float(r.get("score") or 0) for r in completed) / len(completed), 2) if completed else 0.0
    by_problem: dict[str, list[float]] = {}
    plagiarism_alerts = 0
    common_errors: dict[str, int] = {}
    lang_counts: dict[str, int] = {}

    for r in rows:
        pid = str(r.get("problem_id") or "unknown")
        lang = str(r.get("language") or "unknown").lower()
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
        if str(r.get("status")) == "completed":
            by_problem.setdefault(pid, []).append(float(r.get("score") or 0))
        err = str(r.get("error") or "").strip()
        if err:
            common_errors[err] = common_errors.get(err, 0) + 1

        # Best-effort plagiarism alerts from underlying evaluator result JSON.
        rj = r.get("result_json_path")
        if rj:
            try:
                obj = json.loads(Path(str(rj)).read_text(encoding="utf-8"))
            except Exception:
                obj = {}
            plag = obj.get("plagiarism") if isinstance(obj, dict) else None
            if isinstance(plag, dict) and plag.get("detected") is True:
                plagiarism_alerts += 1

    per_problem_avg = {k: round(sum(v) / len(v), 2) for k, v in by_problem.items() if v}
    # Difficulty heuristic: lower average score => higher difficulty.
    difficulty = {}
    for pid, a in per_problem_avg.items():
        if a >= 85:
            difficulty[pid] = "easy"
        elif a >= 60:
            difficulty[pid] = "medium"
        else:
            difficulty[pid] = "hard"

    mistakes = sorted(common_errors.items(), key=lambda x: x[1], reverse=True)[:5]
    metrics = {
        "average_class_score": avg_score,
        "plagiarism_alerts": plagiarism_alerts,
        "problem_average_scores": per_problem_avg,
        "problem_difficulty": difficulty,
        "common_errors": [{"error": k, "count": v} for k, v in mistakes],
        "total_evaluations": len(rows),
        "language_usage": lang_counts,
    }
    try:
        from observability.logger import Trace
        import uuid

        t = Trace(run_id=str(uuid.uuid4()))
        t.set_metric("mode", "admin_dashboard_view")
        t.set_metric("evaluation_metrics", metrics)
        t.flush()
    except Exception:
        pass

    items = ""
    for r in rows:
        rp = r.get("report_path")
        rp_link = f"<a href='/report?path={Path(str(rp)).as_posix()}'>report</a>" if rp else "-"
        items += (
            "<tr>"
            f"<td>{r.get('evaluation_id')}</td>"
            f"<td>{r.get('username')}</td>"
            f"<td>{_esc(r.get('student_name') or '')}</td>"
            f"<td><code>{_esc(r.get('phone') or '')}</code></td>"
            f"<td>{r.get('problem_id')}</td>"
            f"<td>{r.get('language')}</td>"
            f"<td>{r.get('status')}</td>"
            f"<td>{r.get('score')}</td>"
            f"<td>{rp_link}</td>"
            "</tr>"
        )
    metric_html = (
        "<div class='card'>"
        f"<p><strong>Average class score:</strong> {avg_score}</p>"
        f"<p><strong>Plagiarism alerts:</strong> {plagiarism_alerts}</p>"
        f"<p><strong>Total evaluations:</strong> {len(rows)}</p>"
        f"<p><strong>Language usage:</strong> <code>{_esc(lang_counts)}</code></p>"
        "</div>"
    )
    if templates is not None:
        return templates.TemplateResponse(
            "pages/admin.html",
            {"request": request, "title": "Admin Dashboard", "user": _current_user(request), "metrics": metrics, "rows": rows},
        )

    table = _nav(request) + metric_html
    return _html_page("Admin Dashboard", table)


@app.get("/me", response_class=HTMLResponse)
def me(request: Request):
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    username = str(user.get("username") or "")
    rows = list_evaluations_for_user(username, limit=200)

    best: dict[str, float] = {}
    for r in rows:
        pid = str(r.get("problem_id") or "")
        score = float(r.get("score") or 0.0)
        if pid:
            best[pid] = max(best.get(pid, 0.0), score)

    items = ""
    for r in rows[:200]:
        rp = r.get("report_path")
        rp_link = f"<a href='/report?path={Path(str(rp)).as_posix()}'>report</a>" if rp else "-"
        pid = str(r.get("problem_id") or "")
        edit_link = f"<a href='/editor?problem_id={_esc(pid)}'>edit</a>" if pid else "-"
        items += (
            "<tr>"
            f"<td><code>{_esc(r.get('evaluation_id'))}</code></td>"
            f"<td><code>{_esc(pid)}</code></td>"
            f"<td><code>{_esc(r.get('language'))}</code></td>"
            f"<td><code>{_esc(r.get('status'))}</code></td>"
            f"<td><code>{_esc(r.get('score'))}</code></td>"
            f"<td>{rp_link} | {edit_link}</td>"
            "</tr>"
        )

    best_rows = ""
    for pid, score in sorted(best.items(), key=lambda x: (-x[1], x[0]))[:50]:
        best_rows += (
            "<tr>"
            f"<td><code>{_esc(pid)}</code></td>"
            f"<td><code>{_esc(score)}</code></td>"
            f"<td><a href='/assignment?id={_esc(pid)}'>open</a> | <a href='/leaderboard?assignment_id={_esc(pid)}'>leaderboard</a></td>"
            "</tr>"
        )
    if not best_rows:
        best_rows = "<tr><td colspan=3>No submissions yet</td></tr>"

    user_obj = _current_user(request)
    best_list = [{"problem_id": pid, "score": score} for pid, score in sorted(best.items(), key=lambda x: (-x[1], x[0]))]
    subs = []
    for r in rows[:200]:
        subs.append(
            {
                "evaluation_id": r.get("evaluation_id"),
                "problem_id": r.get("problem_id"),
                "language": r.get("language"),
                "status": r.get("status"),
                "score": r.get("score"),
                "report_path": Path(str(r.get("report_path"))).as_posix() if r.get("report_path") else None,
            }
        )

    if templates is not None and user_obj is not None:
        return templates.TemplateResponse(
            "pages/me.html",
            {"request": request, "title": "My Dashboard", "user": user_obj, "best_scores": best_list, "submissions": subs},
        )

    # Fallback
    body = (
        _nav(request)
        + "<div class='card'>"
        f"<p><strong>User:</strong> <code>{_esc(username)}</code></p>"
        "</div>"
        + "<div class='card' style='margin-top:12px'>"
        "<h3 style='margin-top:0'>Best Scores</h3>"
        "<table><thead><tr><th>Problem</th><th>Best Score</th><th></th></tr></thead>"
        f"<tbody>{best_rows}</tbody></table>"
        "</div>"
        + "<div class='card' style='margin-top:12px'>"
        "<h3 style='margin-top:0'>Submission History</h3>"
        "<table><thead><tr><th>Eval</th><th>Problem</th><th>Lang</th><th>Status</th><th>Score</th><th></th></tr></thead>"
        f"<tbody>{items or '<tr><td colspan=6>No evaluations yet</td></tr>'}</tbody></table>"
        "</div>"
        + "<p><a href='/'>Back</a></p>"
    )
    return _render_raw(request, "My Dashboard", body)


@app.get("/student", response_class=HTMLResponse)
def student(request: Request, username: str = ""):
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    role = str(user.get("role") or "").lower()
    if role == "student":
        return RedirectResponse(url="/me", status_code=303)
    target = (username or "").strip().lower()
    if not target:
        return RedirectResponse(url="/instructor/assignments", status_code=303)
    rows = list_evaluations_for_user(target, limit=100)
    items = ""
    for r in rows:
        rp = r.get("report_path")
        rp_link = f"<a href='/report?path={Path(str(rp)).as_posix()}'>report</a>" if rp else "-"
        pid = str(r.get("problem_id") or "")
        items += (
            "<tr>"
            f"<td><code>{_esc(r.get('evaluation_id'))}</code></td>"
            f"<td><code>{_esc(pid)}</code></td>"
            f"<td><code>{_esc(r.get('language'))}</code></td>"
            f"<td><code>{_esc(r.get('status'))}</code></td>"
            f"<td><code>{_esc(r.get('score'))}</code></td>"
            f"<td>{rp_link}</td>"
            "</tr>"
        )
    body = (
        _nav(request)
        + "<div class='card'>"
        f"<p><strong>User:</strong> <code>{_esc(target)}</code></p>"
        "<table><thead><tr><th>Eval</th><th>Problem</th><th>Lang</th><th>Status</th><th>Score</th><th></th></tr></thead>"
        f"<tbody>{items or '<tr><td colspan=6>No evaluations yet</td></tr>'}</tbody></table>"
        "</div>"
        "<p><a href='/instructor/assignments'>Back</a></p>"
    )
    return _html_page("Student View", body)


@app.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    body = _nav(request) + """
    <div class="card">
      <p>Runs OpenAI (gpt-5) agent which calls grading tools via remote MCP.</p>
      <p><strong>Requires:</strong> MCP server exposed publicly (ngrok/cloudflared), and OPENAI_API_KEY set.</p>
      <form action="/ai/run" method="post">
        <label>Student Name</label>
        <input name="student_name" placeholder="Alice" required />

        <label>Problem ID</label>
        <input name="problem_id" placeholder="add_numbers" required />

        <label>Language</label>
        <input name="language" placeholder="python" required />

        <label>Submission Path (absolute path on this machine)</label>
        <input name="submission_path" placeholder="C:\\Automation\\submissions\\alice\\add_numbers\\submission.py" required />

        <button type="submit">Run OpenAI Agent</button>
      </form>
    </div>
    <p><a href="/">Back</a></p>
    """
    return _html_page("AI Agent (OpenAI + MCP)", body)


@app.post("/ai/run", response_class=HTMLResponse)
async def ai_run(
    request: Request,
    student_name: str = Form(...),
    problem_id: str = Form(...),
    language: str = Form(...),
    submission_path: str = Form(...),
):
    guard = _require_role(request, {"instructor", "admin"})
    if isinstance(guard, RedirectResponse):
        return guard
    from assignment_intel.openai_mcp_agent import run_openai_agent
    from datetime import datetime

    try:
        result = run_openai_agent(
            student_name=student_name,
            problem_id=problem_id,
            language=language,
            submission_path=Path(submission_path),
        )
    except Exception as exc:
        return _html_page("AI Error", f"<p><code>{exc}</code></p><p><a href='/ai'>Back</a></p>")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("results") / "agent_reports"
    out_path = out_dir / f"{safe_slug(student_name)}_{safe_slug(problem_id)}_{ts}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    body = (
        _nav(request)
        + "<div class='card'>"
        f"<p>Saved: <code>{out_path}</code></p>"
        f"<p><a href='/report?path={out_path.as_posix()}'>View report JSON</a></p>"
        "</div>"
        "<p><a href='/ai'>Back</a></p>"
    )
    return _html_page("AI Agent Complete", body)


@app.get("/mcp/tools", response_class=HTMLResponse)
def mcp_tools(request: Request):
    tool_names = [
        "run_code",
        "compile_code",
        "evaluate_tests",
        "detect_plagiarism",
        "analyze_complexity",
        "code_quality_analysis",
        "generate_feedback",
        "generate_hidden_test_expansion",
        "generate_problem_metadata",
        "generate_reference_solution",
        "generate_test_cases",
        "compute_expected_outputs",
    ]
    items = "".join(f"<li><code>{name}</code></li>" for name in tool_names)
    return _html_page("MCP Tools", _nav(request) + f"<div class='card'><ul>{items}</ul></div><p><a href='/'>Back</a></p>")


@app.post("/mcp/call")
async def mcp_call(request: Request, payload: dict):
    required = {"tool", "submission_path", "student_name", "problem_id", "language"}
    if not required.issubset(set(payload.keys())):
        return {"ok": False, "error": f"missing keys: {sorted(required - set(payload.keys()))}"}

    tool = str(payload["tool"])
    submission_path = str(payload["submission_path"])
    student_name = str(payload["student_name"])
    problem_id = str(payload["problem_id"])
    language = str(payload["language"])

    # New production tool surface (structured responses)
    if tool == "run_code":
        from platform_mcp.tools_execution import run_code
        from observability.logger import log_tool_call

        res = run_code(
            language=language,
            submission_path=submission_path,
            stdin=str(payload.get("stdin") or ""),
            timeout_s=int(payload.get("timeout_s") or 10),
        )
        log_tool_call(tool="run_code", arguments={"language": language, "submission_path": submission_path}, result=res, source="web:/mcp/call")
        return res
    if tool == "compile_code":
        from platform_mcp.tools_execution import compile_code
        from observability.logger import log_tool_call

        res = compile_code(language=language, submission_path=submission_path)
        log_tool_call(tool="compile_code", arguments={"language": language, "submission_path": submission_path}, result=res, source="web:/mcp/call")
        return res
    if tool == "evaluate_tests":
        from platform_mcp.tools_tests import evaluate_tests
        from observability.logger import log_tool_call

        res = evaluate_tests(
            student_name=student_name,
            problem_id=problem_id,
            language=language,
            submission_path=submission_path,
            extra_hidden_cases_path=str(payload.get("extra_hidden_cases_path") or "") or None,
        )
        log_tool_call(
            tool="evaluate_tests",
            arguments={
                "student_name": student_name,
                "problem_id": problem_id,
                "language": language,
                "submission_path": submission_path,
                "extra_hidden_cases_path": payload.get("extra_hidden_cases_path"),
            },
            result=res,
            source="web:/mcp/call",
        )
        return res
    if tool == "detect_plagiarism":
        from platform_mcp.tools_plagiarism import detect_plagiarism
        from observability.logger import log_tool_call

        res = detect_plagiarism(
            submission_path=submission_path,
            corpus_dir=str(payload.get("corpus_dir") or "submissions"),
            threshold=float(payload.get("threshold") or 0.8),
        )
        log_tool_call(tool="detect_plagiarism", arguments={"submission_path": submission_path}, result=res, source="web:/mcp/call")
        return res
    if tool == "analyze_complexity":
        from platform_mcp.tools_analysis import analyze_complexity
        from observability.logger import log_tool_call

        res = analyze_complexity(language=language, submission_path=submission_path)
        log_tool_call(tool="analyze_complexity", arguments={"language": language, "submission_path": submission_path}, result=res, source="web:/mcp/call")
        return res
    if tool == "code_quality_analysis":
        from platform_mcp.tools_analysis import code_quality_analysis
        from observability.logger import log_tool_call

        res = code_quality_analysis(language=language, submission_path=submission_path)
        log_tool_call(tool="code_quality_analysis", arguments={"language": language, "submission_path": submission_path}, result=res, source="web:/mcp/call")
        return res
    if tool == "generate_feedback":
        from platform_mcp.tools_feedback import generate_feedback
        from observability.logger import log_tool_call

        res = generate_feedback(
            student_name=student_name,
            problem_id=problem_id,
            language=language,
            submission_path=submission_path,
            eval_results=payload.get("eval_results") if isinstance(payload.get("eval_results"), dict) else {},
        )
        log_tool_call(tool="generate_feedback", arguments={"student_name": student_name, "problem_id": problem_id}, result=res, source="web:/mcp/call")
        return res
    if tool == "generate_hidden_test_expansion":
        from platform_mcp.tools_test_expansion import generate_hidden_test_expansion
        from observability.logger import log_tool_call

        res = generate_hidden_test_expansion(problem_id=problem_id, count=int(payload.get("count") or 10))
        log_tool_call(tool="generate_hidden_test_expansion", arguments={"problem_id": problem_id, "count": int(payload.get("count") or 10)}, result=res, source="web:/mcp/call")
        return res
    if tool == "generate_problem_metadata":
        from platform_mcp.tools_problem_gen import generate_problem_metadata
        from observability.logger import log_tool_call

        res = generate_problem_metadata(title=str(payload.get("title") or ""), problem_description=str(payload.get("problem_description") or ""))
        log_tool_call(tool="generate_problem_metadata", arguments={"title": payload.get("title")}, result=res, source="web:/mcp/call")
        return res
    if tool == "generate_reference_solution":
        from platform_mcp.tools_problem_gen import generate_reference_solution
        from observability.logger import log_tool_call

        res = generate_reference_solution(
            title=str(payload.get("title") or ""),
            problem_description=str(payload.get("problem_description") or ""),
            constraints=str(payload.get("constraints") or ""),
            examples=payload.get("examples") if isinstance(payload.get("examples"), list) else [],
        )
        log_tool_call(tool="generate_reference_solution", arguments={"title": payload.get("title")}, result=res, source="web:/mcp/call")
        return res
    if tool == "generate_test_cases":
        from platform_mcp.tools_problem_gen import generate_test_cases
        from observability.logger import log_tool_call

        res = generate_test_cases(
            title=str(payload.get("title") or ""),
            problem_description=str(payload.get("problem_description") or ""),
            constraints=str(payload.get("constraints") or ""),
            difficulty=str(payload.get("difficulty") or "medium"),
            visible_count=int(payload.get("visible_count") or 3),
            hidden_count=int(payload.get("hidden_count") or 10),
            stress_count=int(payload.get("stress_count") or 20),
        )
        log_tool_call(tool="generate_test_cases", arguments={"title": payload.get("title")}, result=res, source="web:/mcp/call")
        return res
    if tool == "compute_expected_outputs":
        from platform_mcp.tools_problem_gen import compute_expected_outputs
        from observability.logger import log_tool_call

        inputs = payload.get("inputs") if isinstance(payload.get("inputs"), list) else []
        inputs_str = [str(x) for x in inputs]
        res = compute_expected_outputs(reference_solution_code=str(payload.get("reference_solution_code") or ""), inputs=inputs_str, timeout_s=int(payload.get("timeout_s") or 8))
        log_tool_call(tool="compute_expected_outputs", arguments={"inputs_count": len(inputs_str)}, result=res, source="web:/mcp/call")
        return res

    # Backwards compatible tool surface
    req = ToolCall(tool=tool, submission_path=submission_path, student_name=student_name, problem_id=problem_id, language=language)
    return call_tool(req)


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request):
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    role = str(user.get("role") or "").lower()
    if role == "student":
        return RedirectResponse(
            url="/me?toast=Use%20Dashboard%20to%20view%20your%20submission%20reports.",
            status_code=303,
        )
    report_dir = Path("results") / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(report_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]
    rows = []
    import datetime as _dt

    for p in files:
        rows.append(
            {
                "name": p.name,
                "path": p.as_posix(),
                "mtime": _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    if templates is not None:
        return templates.TemplateResponse(
            "pages/reports.html",
            {"request": request, "title": "Reports", "user": _current_user(request), "reports": rows},
        )

    links = "".join(f"<li><a href='/report?path={_esc(r['path'])}'>{_esc(r['name'])}</a></li>" for r in rows)
    body = f"<div class='card'><ul>{links or '<li>No reports yet</li>'}</ul></div><p><a href='/'>Back</a></p>"
    return _html_page("Reports", body)


@app.get("/report", response_class=HTMLResponse)
def report(request: Request, path: str):
    user = _require_role(request, {"student", "instructor", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    role = str(user.get("role") or "").lower()
    username = str(user.get("username") or "").strip().lower()

    # Block traversal: only allow files inside this repo under results/.
    report_path = Path(path)
    repo_root = Path(__file__).resolve().parent
    try:
        resolved = report_path.resolve()
    except Exception:
        return _html_page("Not Found", "<p>Report not found.</p><p><a href='/me'>Back</a></p>")
    try:
        rel = resolved.relative_to(repo_root)
    except Exception:
        return _html_page("Blocked", "<p>Report path is outside this project.</p><p><a href='/me'>Back</a></p>")
    if not rel.parts or rel.parts[0].lower() != "results":
        return _html_page("Blocked", "<p>Can only open reports stored under <code>results/</code>.</p><p><a href='/me'>Back</a></p>")

    # Students can only open their own reports.
    if role == "student":
        fn = resolved.name.lower()
        if not (fn.startswith(username + "_") or fn.startswith(username + "-")):
            return RedirectResponse(url="/me?toast=You%20can%20only%20view%20your%20own%20reports.", status_code=303)

    if not resolved.exists() or resolved.suffix.lower() != ".json":
        return _html_page("Not Found", "<p>Report not found.</p><p><a href='/me'>Back</a></p>")

    try:
        obj = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return _html_page("Error", "<p>Could not read report.</p><p><a href='/me'>Back</a></p>")

    if not isinstance(obj, dict):
        pretty = json.dumps(obj, indent=2)
        body = f"<div class='card'><pre style='white-space:pre-wrap'>{_esc(pretty)}</pre></div><p><a href='/me'>Back</a></p>"
        return _html_page("Report", body)

    # Detect report shape and render an interactive view.
    if isinstance(obj.get("analysis"), dict) and ("tools" in obj or isinstance(obj.get("tools"), list)):
        if templates is not None:
            analysis = obj.get("analysis") if isinstance(obj.get("analysis"), dict) else {}
            results = analysis.get("results") if isinstance(analysis.get("results"), dict) else {}
            complexity = analysis.get("complexity") if isinstance(analysis.get("complexity"), dict) else {}
            relevance = analysis.get("relevance") if isinstance(analysis.get("relevance"), dict) else {}
            score = float(results.get("score", 0.0) or 0.0)
            total = int(results.get("total_test_cases", 0) or 0)
            passed = int(results.get("passed_cases", 0) or 0)
            visible = results.get("visible") if isinstance(results.get("visible"), dict) else {}
            hidden = results.get("hidden") if isinstance(results.get("hidden"), dict) else {}
            anti = results.get("anti_cheat") if isinstance(results.get("anti_cheat"), dict) else {}
            plag = results.get("plagiarism") if isinstance(results.get("plagiarism"), dict) else {}
            cr = results.get("case_results") if isinstance(results.get("case_results"), list) else []
            vis_cases = []
            for c in cr:
                if not isinstance(c, dict):
                    continue
                if str(c.get("visibility")) != "visible":
                    continue
                vis_cases.append(
                    {
                        "input": str(c.get("input") or ""),
                        "expected": str(c.get("expected") or ""),
                        "actual": str(c.get("actual") or ""),
                        "passed": bool(c.get("passed")),
                    }
                )

            return templates.TemplateResponse(
                "pages/report.html",
                {
                    "request": request,
                    "title": "Report",
                    "user": _current_user(request),
                    "student": str(obj.get("student_name") or ""),
                    "problem_id": str(obj.get("problem_id") or ""),
                    "language": str(obj.get("language") or ""),
                    "submitted_at": str(obj.get("submitted_at") or ""),
                    "score": score,
                    "total": total,
                    "passed": passed,
                    "visible": {
                        "passed": int(visible.get("passed", 0) or 0),
                        "total": int(visible.get("total", 0) or 0),
                        "weighted_contribution": float(visible.get("weighted_contribution", 0.0) or 0.0),
                    },
                    "hidden": {
                        "passed": int(hidden.get("passed", 0) or 0),
                        "total": int(hidden.get("total", 0) or 0),
                        "weighted_contribution": float(hidden.get("weighted_contribution", 0.0) or 0.0),
                    },
                    "anti_cheat_passed": bool(anti.get("passed")),
                    "plagiarism_detected": bool(plag.get("detected")),
                    "case_results": vis_cases,
                    "feedback": str(obj.get("feedback") or ""),
                    "hints": obj.get("hints") if isinstance(obj.get("hints"), list) else [],
                    "relevance": relevance,
                    "complexity": complexity,
                    "tools": obj.get("tools") if isinstance(obj.get("tools"), list) else [],
                    "stdout": str(results.get("stdout") or ""),
                    "stderr": str(results.get("stderr") or ""),
                    "agent_plan": obj.get("agent_plan") if isinstance(obj.get("agent_plan"), list) else [],
                    "agent_trace": obj.get("agent_trace") if isinstance(obj.get("agent_trace"), list) else [],
                    "ai_feedback": analysis.get("ai_feedback") if isinstance(analysis.get("ai_feedback"), dict) else (obj.get("ai_feedback") if isinstance(obj.get("ai_feedback"), dict) else {}),
                },
            )

        body = _render_eval_report(obj) + "<p style='margin-top:12px'><a href='/me'>Back</a></p>"
        return _html_page("Report", body)

    if "agents" in obj or "final_report" in obj or "planner" in obj or "execution" in obj:
        body = _render_agent_report(obj) + "<p style='margin-top:12px'><a href='/me'>Back</a></p>"
        return _html_page("Agent Report", body)

    pretty = json.dumps(obj, indent=2)
    body = f"<div class='card'><pre style='white-space:pre-wrap'>{_esc(pretty)}</pre></div><p><a href='/me'>Back</a></p>"
    return _html_page("Report", body)


@app.get("/editor", response_class=HTMLResponse)
def editor(request: Request, problem_id: str, username: str = ""):
    # Only students can access the editor to modify/resubmit solutions.
    # Instructors have a separate read-only submission viewer in /instructor/submission.
    user = _require_role(request, {"student", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    from assignment_intel.db import get_latest_submission_for_user_problem, get_assignment
    from assignment_intel.language import detect_language

    target_user = str(user.get("username") or "")
    sub = get_latest_submission_for_user_problem(username=target_user, problem_id=problem_id)
    if not sub:
        return _html_page(
            "Editor",
            _nav(request)
            + _presence_widget(f"assignment:{problem_id}")
            + "<div class='card'><p>No prior submission found for this user/problem.</p>"
            + f"<p><a href='/?problem_id={_esc(problem_id)}'>Go submit first</a></p></div>",
        )

    submission_path = Path(str(sub.get("submission_path") or ""))
    try:
        code = submission_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        code = ""
    lang = str(sub.get("language") or detect_language(filename=str(submission_path)))

    a = get_assignment(assignment_id=problem_id) or {}
    statement = str(a.get("generated_description") or a.get("description") or "")

    starters = {
        "python": "import sys\n\n\ndef main():\n    data = sys.stdin.read().strip().split()\n    # TODO: implement\n    print(\"\")\n\n\nif __name__ == \"__main__\":\n    main()\n",
        "javascript": "const fs = require('fs');\n\nfunction main() {\n  const data = fs.readFileSync(0,'utf8').trim().split(/\\s+/);\n  // TODO: implement\n  process.stdout.write(\"\\n\");\n}\n\nmain();\n",
        "java": "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n  public static void main(String[] args) throws Exception {\n    String input = new String(System.in.readAllBytes());\n    String[] data = input.trim().isEmpty() ? new String[0] : input.trim().split(\"\\\\s+\");\n    // TODO: implement\n    System.out.println(\"\");\n  }\n}\n",
        "c": "#include <stdio.h>\n\nint main() {\n  // TODO: read stdin, solve, print stdout\n  return 0;\n}\n",
        "cpp": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n  ios::sync_with_stdio(false);\n  cin.tie(nullptr);\n  // TODO: read stdin, solve, print stdout\n  return 0;\n}\n",
    }

    if templates is not None:
        return templates.TemplateResponse(
            "pages/editor.html",
            {
                "request": request,
                "user": _current_user(request),
                "problem_id": problem_id,
                "title": "Editor",
                "problem_title": str(a.get("title") or problem_id),
                "statement": statement,
                "language": lang if lang in starters else "python",
                "code": code or starters.get(lang, starters["python"]),
                "starters": starters,
            },
        )

    body = _nav(request) + "<pre>" + _esc(statement) + "</pre>"
    return _html_page("Editor", body)


@app.post("/editor/submit", response_class=HTMLResponse)
async def editor_submit(
    request: Request,
    problem_id: str = Form(...),
    language: str = Form(...),
    code: str = Form(...),
):
    # Only students can run/submit from the editor.
    user = _require_role(request, {"student", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    username = str(user.get("username") or "")
    phone = str(user.get("phone") or "") if user.get("phone") else ""
    # Save edited code under the same student/problem folder.
    try:
        stored = save_submission_text(student_name=username, problem_id=problem_id, language=language, code=code, versioned=True)
    except Exception as exc:
        return _html_page("Editor Error", _nav(request) + f"<div class='card'><p><code>{_esc(exc)}</code></p><p><a href='/editor?problem_id={_esc(problem_id)}'>Back</a></p></div>")

    from assignment_intel.eval_service import enqueue_only
    from assignment_intel.db import enqueue_job

    evaluation_id = enqueue_only(stored=stored, student_name=username, phone=phone.strip() or None)
    enqueue_job(job_type="solution_evaluation", payload={"evaluation_id": evaluation_id})
    return RedirectResponse(url=f"/evaluation?id={evaluation_id}", status_code=303)


@app.post("/submission/delete", response_class=HTMLResponse)
async def submission_delete(request: Request, evaluation_id: str = Form(...)):
    """Student deletes one of their submissions (and its evaluation)."""
    user = _require_role(request, {"student", "admin"})
    if isinstance(user, RedirectResponse):
        return user
    from assignment_intel.db import delete_submission, get_evaluation, get_submission

    try:
        eid = int(evaluation_id)
    except ValueError:
        eid = 0
    if not eid:
        return RedirectResponse(url="/me", status_code=303)

    ev = get_evaluation(eid)
    if not ev:
        return RedirectResponse(url="/me", status_code=303)
    sub = get_submission(ev.submission_id)
    if not sub:
        return RedirectResponse(url="/me", status_code=303)

    role = str(user.get("role") or "").lower()
    if role != "admin" and str(sub.get("username") or "").lower() != str(user.get("username") or "").lower():
        return RedirectResponse(url="/me", status_code=303)

    # Best-effort: remove file from disk (leave directories intact).
    try:
        p = Path(str(sub.get("submission_path") or ""))
        if p.exists():
            p.unlink()
    except Exception:
        pass
    delete_submission(submission_id=int(sub.get("id") or 0))
    return RedirectResponse(url="/me", status_code=303)


def _start_background_worker() -> None:
    """Start the job worker in a daemon thread when no separate worker process exists.

    On Render free tier there is no background worker service, so we run the
    worker loop inside the web process itself. Set DISABLE_EMBEDDED_WORKER=1
    to turn this off (e.g. when you have a dedicated worker dyno/service).
    """
    if os.environ.get("DISABLE_EMBEDDED_WORKER", "").strip() == "1":
        return
    import threading

    def _worker_loop() -> None:
        try:
            # Re-use the same worker logic
            import time as _time
            from assignment_intel.db import claim_next_job, update_job_finished
            from worker import process_job
            print("[embedded-worker] started", flush=True)
            while True:
                try:
                    job = claim_next_job(types=["problem_generation", "solution_evaluation"])
                    if job:
                        try:
                            process_job(job)
                        except Exception as exc:
                            try:
                                update_job_finished(job_id=int(job["id"]), status="failed", error=str(exc), result=None)
                            except Exception:
                                pass
                except Exception:
                    pass
                _time.sleep(1.0)
        except Exception as exc:
            print(f"[embedded-worker] fatal: {exc}", flush=True)

    t = threading.Thread(target=_worker_loop, daemon=True, name="embedded-worker")
    t.start()
    print("[embedded-worker] thread launched", flush=True)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
#  REST API  â€”  /api/*  routes for the React frontend
#  All return JSON. The old Jinja2 HTML routes still work in parallel.
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

@app.post("/api/auth/register")
async def api_register(payload: dict):
    from urllib.parse import quote
    from assignment_intel.auth import hash_password, issue_session_token
    from assignment_intel.db import count_users, create_user, get_user_by_email, get_user_by_username

    u = (payload.get("username") or "").strip().lower()
    e = (payload.get("email") or "").strip().lower()
    pw = payload.get("password") or ""

    if not u or not e or not pw:
        return JSONResponse({"error": "username, email and password are required"}, status_code=400)
    if get_user_by_username(u) or get_user_by_email(e):
        return JSONResponse({"error": "User already exists"}, status_code=409)

    role = "admin" if count_users() == 0 else "student"
    try:
        pw_hash = hash_password(pw)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    uid = create_user(username=u, email=e, password_hash=pw_hash, phone=None, role=role)
    token = issue_session_token(user_id=uid, username=u, role=role)
    return JSONResponse({"token": token, "username": u, "role": role, "id": uid})


@app.post("/api/auth/login")
async def api_login(payload: dict):
    from assignment_intel.auth import issue_session_token, verify_password
    from assignment_intel.db import get_user_by_email, get_user_by_username

    ident = (payload.get("username") or payload.get("identity") or "").strip().lower()
    pw = payload.get("password") or ""
    user = get_user_by_username(ident) or get_user_by_email(ident)
    if not user or not verify_password(pw, str(user.get("password_hash") or "")):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    token = issue_session_token(
        user_id=int(user["id"]),
        username=str(user["username"]),
        role=str(user["role"])
    )
    return JSONResponse({
        "token": token,
        "username": str(user["username"]),
        "role": str(user["role"]),
        "id": int(user["id"])
    })


@app.get("/api/me")
async def api_me(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    from assignment_intel.db import list_evaluations_for_user
    username = str(user.get("username") or "")
    evals = list_evaluations_for_user(username=username, limit=20)
    best: dict[str, float] = {}
    subs = []
    for ev in evals:
        pid = str(ev.get("problem_id") or "")
        sc = float(ev.get("score") or 0.0)
        if pid not in best or sc > best[pid]:
            best[pid] = sc
        subs.append({
            "evaluation_id": str(ev.get("evaluation_id") or ""),
            "problem_id": pid,
            "language": str(ev.get("language") or ""),
            "status": str(ev.get("status") or ""),
            "score": sc,
            "report_path": str(ev.get("report_path") or "") if ev.get("report_path") else None,
        })
    return JSONResponse({
        "username": user.get("username"),
        "role": user.get("role"),
        "best_scores": [{"problem_id": k, "score": v} for k, v in best.items()],
        "submissions": subs,
    })


@app.get("/api/assignments")
async def api_assignments():
    from assignment_intel.db import list_student_assignments
    rows = list_student_assignments()
    return JSONResponse({"assignments": [dict(r) for r in rows]})


@app.get("/api/assignment/{assignment_id}")
async def api_assignment(assignment_id: str):
    from assignment_intel.db import get_assignment, list_test_cases
    a = get_assignment(assignment_id=assignment_id)
    if not a:
        return JSONResponse({"error": "Not found"}, status_code=404)
    cases = list_test_cases(assignment_id=assignment_id)
    visible = [
        {"id": c.get("id"), "input": c.get("input_text"), "expected": c.get("expected_output")}
        for c in cases if str(c.get("visibility")) == "visible"
    ]
    try:
        tags = json.loads(a.get("tags_json") or "[]")
    except Exception:
        tags = []
    try:
        examples = json.loads(a.get("examples_json") or "[]")
    except Exception:
        examples = []
    return JSONResponse({
        "id": str(a.get("id") or ""),
        "title": str(a.get("title") or ""),
        "description": str(a.get("generated_description") or a.get("description") or ""),
        "difficulty": str(a.get("difficulty") or ""),
        "constraints": str(a.get("constraints_text") or ""),
        "input_format": str(a.get("input_format") or ""),
        "output_format": str(a.get("output_format") or ""),
        "tags": tags,
        "examples": examples,
        "visible_tests": int(a.get("visible_tests") or 0),
        "hidden_tests": int(a.get("hidden_tests") or 0),
        "active": bool(a.get("active")),
        "visible_cases": visible,
    })


@app.post("/api/submit")
async def api_submit(request: Request, problem_id: str = Form(...), file: UploadFile = File(...)):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    username = str(user.get("username") or "")
    try:
        content = await file.read()
        stored = save_submission_bytes(
            student_name=username,
            problem_id=problem_id,
            filename=file.filename or "submission.py",
            content=content,
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    from assignment_intel.eval_service import enqueue_only
    from assignment_intel.db import enqueue_job
    evaluation_id = enqueue_only(stored=stored, student_name=username)
    job_id = enqueue_job(job_type="solution_evaluation", payload={"evaluation_id": evaluation_id})
    return JSONResponse({
        "evaluation_id": evaluation_id,
        "job_id": job_id,
        "problem_id": stored.problem_id,
        "language": stored.language,
    })


@app.get("/api/evaluation/{evaluation_id}")
async def api_evaluation(evaluation_id: int, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    from assignment_intel.db import get_evaluation, get_submission
    ev = get_evaluation(evaluation_id)
    if not ev:
        return JSONResponse({"error": "Not found"}, status_code=404)
    sub = get_submission(ev.submission_id)
    return JSONResponse({
        "id": ev.id,
        "status": str(ev.status),
        "score": float(ev.score or 0),
        "error": str(ev.error or ""),
        "problem_id": str(sub.get("problem_id") or "") if sub else "",
        "language": str(sub.get("language") or "") if sub else "",
        "report_path": str(ev.report_path) if ev.report_path else None,
    })


@app.get("/api/report/{evaluation_id}")
async def api_report(evaluation_id: int, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    from assignment_intel.db import get_evaluation, get_submission
    ev = get_evaluation(evaluation_id)
    if not ev or not ev.report_path:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    rp = Path(str(ev.report_path))
    if not rp.exists():
        return JSONResponse({"error": "Report file missing"}, status_code=404)
    try:
        data = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"error": "Could not read report"}, status_code=500)
    return JSONResponse(data)


@app.get("/api/leaderboard/{assignment_id}")
async def api_leaderboard(assignment_id: str):
    from assignment_intel.db import get_leaderboard
    rows = get_leaderboard(assignment_id=assignment_id, limit=50)
    return JSONResponse({"assignment_id": assignment_id, "rows": [dict(r) for r in rows]})


@app.get("/api/health")
async def api_health():
    return JSONResponse({"status": "ok", "service": "evaluator-engine"})


# â”€â”€ AI Natural Language Edit API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/workflows/{wf_id}/ai-edit")
async def api_ai_edit_preview(wf_id: str, request: Request):
    """
    Preview an AI-powered NL edit without saving.
    Returns the updated workflow + diff summary + changes list.
    """
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    wf = _load_wf(wf_id)
    if wf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    command = str(payload.get("command", "")).strip()
    if not command:
        return JSONResponse({"error": "command is required"}, status_code=422)

    try:
        from ai_builder.builder import WorkflowBuilder
        builder = WorkflowBuilder()
        result  = builder.edit(wf, command)
    except Exception as e:
        return JSONResponse({"error": f"AI builder unavailable: {e}"}, status_code=503)

    if not result.success:
        return JSONResponse({
            "success": False,
            "error":   result.error,
        }, status_code=422)

    return JSONResponse({
        "success":          True,
        "command_parsed":   result.command_parsed,
        "changes":          result.changes,
        "diff_summary":     result.diff_summary,
        "updated_workflow": result.updated_workflow,
        "original_nodes":   len(wf.get("nodes", [])),
        "updated_nodes":    len((result.updated_workflow or {}).get("nodes", [])),
    })


@app.post("/api/workflows/{wf_id}/ai-edit/apply")
async def api_ai_edit_apply(wf_id: str, request: Request):
    """
    Apply a previewed edit: accepts the updated_workflow JSON and saves it.
    No second AI call â€” the frontend sends back what the preview returned.
    """
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    wf = _load_wf(wf_id)
    if wf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    updated = payload.get("updated_workflow")
    if not updated or not isinstance(updated, dict):
        return JSONResponse({"error": "updated_workflow is required"}, status_code=422)

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Preserve the ID and audit fields; accept node/edge changes only
    for key in ("nodes", "edges", "variables", "triggers"):
        if key in updated:
            wf[key] = updated[key]
    wf["updated_at"] = now
    wf.setdefault("metadata", {})["updated_at"] = now

    _save_wf(wf)
    return JSONResponse({"success": True, "workflow": wf})


# â”€â”€ Workflow Execution API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/workflows/{wf_id}/run")
async def api_workflow_run(wf_id: str, request: Request):
    """Start a workflow execution. Returns run_id immediately."""
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    wf = _load_wf(wf_id)
    if wf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        payload  = await request.json()
    except Exception:
        payload  = {}

    dry_run = payload.get("dry_run", True)
    inputs  = payload.get("inputs", {}) or {}

    # Validate first
    from workflows.engine import WorkflowEngine
    engine = WorkflowEngine(dry_run=dry_run)
    engine._workflow = wf
    errors = engine.validate()
    if errors:
        return JSONResponse({"error": "Validation failed", "details": errors}, status_code=422)

    from workflows.executor import start_run
    run_id = start_run(wf, dry_run=dry_run, inputs=inputs)

    return JSONResponse({"run_id": run_id, "workflow_id": wf_id, "dry_run": dry_run}, status_code=202)


@app.get("/api/workflows/{wf_id}/runs")
async def api_workflow_runs(wf_id: str, request: Request):
    """List past runs for a workflow (newest first)."""
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    from workflows.executor import list_runs
    return JSONResponse({"runs": list_runs(wf_id)})


@app.get("/api/runs/{run_id}")
async def api_run_status(run_id: str, request: Request):
    """Get the current status and logs of a specific run."""
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    from workflows.executor import get_run, RUN_REGISTRY
    rec = get_run(run_id)
    if rec is None:
        return JSONResponse({"error": "Run not found"}, status_code=404)
    return JSONResponse(rec)


@app.get("/api/runs/{run_id}/stream")
async def api_run_stream(run_id: str, request: Request):
    """
    Server-Sent Events stream for real-time execution updates.

    Auth (in priority order):
      1. Authorization: Bearer <token>   (normal API calls)
      2. Cookie: session=<token>         (Jinja2 pages)
      3. ?token=<token>                  (EventSource fallback â€” validated server-side)
    """
    from fastapi.responses import StreamingResponse, Response
    from workflows.executor import get_run

    # â”€â”€ Auth: check header / cookie / query param â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _resolve_token() -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        cookie = request.cookies.get("session")
        if cookie:
            return cookie
        return request.query_params.get("token")  # EventSource fallback

    token = _resolve_token()
    if not token:
        return Response("Unauthorized", status_code=401)
    try:
        from assignment_intel.auth import decode_session_token
        decode_session_token(token)   # raises if invalid/expired
    except Exception:
        return Response("Unauthorized", status_code=401)

    async def event_generator():
        terminal       = {"succeeded", "failed", "cancelled", "timed_out"}
        last_log_count = 0
        idle_ticks     = 0
        max_idle       = 240   # 2-minute max with no activity (240 Ã— 0.5s)

        while True:
            if await request.is_disconnected():
                break

            rec = get_run(run_id)
            if rec is None:
                yield f"data: {json.dumps({'error': 'run not found'})}\n\n"
                break

            log_count = len(rec.get("logs", []))
            status_changed = rec.get("status") != rec.get("_last_sent_status")
            if log_count != last_log_count or status_changed:
                last_log_count = log_count
                rec["_last_sent_status"] = rec.get("status")
                yield f"data: {json.dumps(rec, default=str)}\n\n"
                idle_ticks = 0
            else:
                idle_ticks += 1

            if rec.get("status") in terminal:
                yield f"data: {json.dumps(rec, default=str)}\n\n"
                break

            if idle_ticks > max_idle:
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )



# â”€â”€ Workflow API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Stored as JSON files under ./workflows/saved/<id>.json so no extra DB is needed.

_WF_DIR = Path("workflows") / "saved"


def _wf_path(wf_id: str) -> Path:
    _WF_DIR.mkdir(parents=True, exist_ok=True)
    return _WF_DIR / f"{wf_id}.json"


def _load_wf(wf_id: str) -> dict | None:
    p = _wf_path(wf_id)
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def _save_wf(wf: dict) -> None:
    _WF_DIR.mkdir(parents=True, exist_ok=True)
    with _wf_path(wf["id"]).open("w", encoding="utf-8") as f:
        json.dump(wf, f, indent=2)


@app.get("/api/workflows")
async def api_workflows_list(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    _WF_DIR.mkdir(parents=True, exist_ok=True)
    workflows = []
    for p in sorted(_WF_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with p.open(encoding="utf-8") as f:
                wf = json.load(f)
            workflows.append({
                "id":          wf.get("id"),
                "name":        wf.get("name", "Untitled"),
                "description": wf.get("description", ""),
                "updated_at":  wf.get("updated_at"),
                "node_count":  len(wf.get("nodes", [])),
            })
        except Exception:
            pass
    return JSONResponse({"workflows": workflows})


@app.get("/api/workflows/{wf_id}")
async def api_workflow_get(wf_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    wf = _load_wf(wf_id)
    if wf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(wf)


@app.post("/api/workflows")
async def api_workflow_create(payload: dict, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    wf_id = str(uuid.uuid4())
    now   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    wf = {
        "id":           wf_id,
        "schema_version": "1.0",
        "workflow_id":  wf_id,
        "name":         str(payload.get("name", "New Workflow"))[:100],
        "description":  str(payload.get("description", ""))[:500],
        "version":      "1.0.0",
        "metadata":     {"owner": user.get("username", ""), "created_at": now, "updated_at": now},
        "settings":     {"execution_mode": "dag", "max_concurrent_runs": 5, "timeout": "PT10M"},
        "variables":    [],
        "triggers":     [{"id": "manual_trigger", "type": "manual"}],
        "nodes":        payload.get("nodes", []),
        "edges":        payload.get("edges", []),
        "created_at":   now,
        "updated_at":   now,
    }
    _save_wf(wf)
    return JSONResponse(wf, status_code=201)


@app.put("/api/workflows/{wf_id}")
async def api_workflow_update(wf_id: str, payload: dict, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    wf = _load_wf(wf_id)
    if wf is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Only allow patching safe fields
    for key in ("name", "description", "nodes", "edges", "variables", "triggers", "settings"):
        if key in payload:
            wf[key] = payload[key]
    wf["updated_at"] = now
    wf.setdefault("metadata", {})["updated_at"] = now
    _save_wf(wf)
    # â”€â”€ Persist a version snapshot â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _save_wf_version(wf_id, wf)
    # â”€â”€ Reload trigger runtime so new cron/webhook triggers register
    try:
        from workflows.trigger_runtime import get_runtime
        get_runtime().reload()
    except Exception:
        pass
    return JSONResponse(wf)


@app.delete("/api/workflows/{wf_id}")
async def api_workflow_delete(wf_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    p = _wf_path(wf_id)
    if not p.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    p.unlink()
    try:
        from workflows.trigger_runtime import get_runtime
        get_runtime().reload()
    except Exception:
        pass
    return JSONResponse({"ok": True})


# â”€â”€ Webhook trigger ingress â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/webhooks/{path:path}")
async def api_webhook_trigger(path: str, request: Request):
    """
    Receive an inbound webhook and fire the matching workflow.
    URL pattern: /api/webhooks/<whatever-path-is-in-trigger.webhook_path>
    """
    canonical = f"/webhooks/{path}"
    try:
        from workflows.trigger_runtime import get_runtime
        match = get_runtime().get_webhook_workflow(canonical)
    except Exception:
        match = None

    if not match:
        return JSONResponse({"error": "No workflow registered for this webhook"}, status_code=404)

    wf_id, trigger_id = match
    wf = _load_wf(wf_id)
    if not wf:
        return JSONResponse({"error": "Workflow not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    from workflows.executor import start_run
    run_id = start_run(wf, dry_run=False, inputs={"_trigger": "webhook",
                                                   "_trigger_id": trigger_id,
                                                   "payload": body})
    return JSONResponse({"run_id": run_id, "workflow_id": wf_id}, status_code=202)


# â”€â”€ Workflow Versioning â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_VER_DIR = Path("workflows") / "versions"


def _ver_dir(wf_id: str) -> Path:
    d = _VER_DIR / wf_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_wf_version(wf_id: str, wf: Dict) -> None:
    """Save a snapshot of the workflow as a new version."""
    d   = _ver_dir(wf_id)
    ver = int(time.time())
    p   = d / f"{ver}.json"
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2)
        # Keep only last 20 versions
        versions = sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime)
        for old in versions[:-20]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


@app.get("/api/workflows/{wf_id}/versions")
async def api_workflow_versions(wf_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    d = _ver_dir(wf_id)
    versions = []
    for p in sorted(d.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        try:
            with p.open(encoding="utf-8") as f:
                wf = json.load(f)
            versions.append({
                "version_ts":  p.stem,
                "saved_at":    wf.get("updated_at"),
                "node_count":  len(wf.get("nodes", [])),
                "name":        wf.get("name", "Untitled"),
            })
        except Exception:
            pass
    return JSONResponse({"versions": versions})


@app.post("/api/workflows/{wf_id}/versions/{version_ts}/restore")
async def api_workflow_restore(wf_id: str, version_ts: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    p = _ver_dir(wf_id) / f"{version_ts}.json"
    if not p.exists():
        return JSONResponse({"error": "Version not found"}, status_code=404)
    with p.open(encoding="utf-8") as f:
        wf = json.load(f)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    wf["updated_at"] = now
    _save_wf(wf)
    return JSONResponse({"success": True, "workflow": wf})


# â”€â”€ Human Approval â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_APPROVAL_SIGNALS: Dict[str, asyncio.Event] = {}  # run_id -> Event
_APPROVAL_DECISIONS: Dict[str, Dict] = {}          # run_id -> {approved, approver, comment}

import json
from pathlib import Path

def _write_approval(run_id: str, decision: dict):
    _APPROVAL_DECISIONS[run_id] = decision
    if run_id in _APPROVAL_SIGNALS:
        _APPROVAL_SIGNALS[run_id].set()
    
    appr_dir = Path('workflows/approvals')
    appr_dir.mkdir(parents=True, exist_ok=True)
    (appr_dir / f'{run_id}.json').write_text(json.dumps(decision), encoding='utf-8')

@app.post("/api/runs/{run_id}/approve")
async def api_run_approve(run_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    _write_approval(run_id, {
        "approved": True,
        "approver": user.get("username", "unknown"),
        "comment":  body.get("comment", ""),
    })
    return JSONResponse({"ok": True, "run_id": run_id})


@app.post("/api/runs/{run_id}/reject")
async def api_run_reject(run_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    _write_approval(run_id, {
        "approved": False,
        "approver": user.get("username", "unknown"),
        "comment":  body.get("comment", "Rejected"),
    })
    return JSONResponse({"ok": True, "run_id": run_id})


@app.post("/api/runs/{run_id}/reject")
async def api_run_reject(run_id: str, request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _APPROVAL_DECISIONS[run_id] = {
        "approved": False,
        "approver": user.get("username", "unknown"),
        "comment":  body.get("comment", "Rejected"),
    }
    if run_id in _APPROVAL_SIGNALS:
        _APPROVAL_SIGNALS[run_id].set()
    return JSONResponse({"ok": True, "run_id": run_id})


# â”€â”€ Plugin Configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/plugin-configs")
async def api_plugin_configs_get(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from workflows.executor import _load_plugin_configs
    configs = _load_plugin_configs()
    # Strip secrets before sending to frontend
    safe = {}
    for pid, cfg in configs.items():
        safe[pid] = {"config": cfg.get("config", {}), "has_secrets": bool(cfg.get("secrets"))}
    return JSONResponse({"configs": safe})


@app.post("/api/plugin-configs")
async def api_plugin_configs_save(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Merge with existing (never overwrite secrets with empty values)
    from workflows.executor import _load_plugin_configs, save_plugin_configs
    existing = _load_plugin_configs()
    for plugin_id, cfg in body.items():
        entry = existing.setdefault(plugin_id, {"config": {}, "secrets": {}})
        entry["config"].update(cfg.get("config", {}))
        for k, v in cfg.get("secrets", {}).items():
            if v:  # only overwrite if non-empty
                entry["secrets"][k] = v
    save_plugin_configs(existing)
    return JSONResponse({"ok": True})


# â”€â”€ Example Workflow Catalogue â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.get("/api/workflow-examples")
async def api_workflow_examples(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    examples_dir = Path("workflows") / "examples"
    examples = []
    if examples_dir.exists():
        for p in sorted(examples_dir.glob("*.json")):
            try:
                with p.open(encoding="utf-8") as f:
                    wf = json.load(f)
                examples.append({
                    "file":        p.name,
                    "name":        wf.get("name", p.stem),
                    "description": wf.get("description", ""),
                    "node_count":  len(wf.get("nodes", [])),
                    "workflow":    wf,
                })
            except Exception:
                pass
    return JSONResponse({"examples": examples})


# â”€â”€ AI Workflow Generation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.post("/api/ai/generate-workflow")
async def api_ai_generate_workflow(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    intent = str(body.get("intent", "")).strip()
    if not intent:
        return JSONResponse({"error": "intent is required"}, status_code=422)
    try:
        from ai_builder.builder import WorkflowBuilder
        result = WorkflowBuilder().build(intent)
    except Exception as e:
        return JSONResponse({"error": f"AI builder unavailable: {e}"}, status_code=503)
    if not result.success:
        return JSONResponse({"error": result.error}, status_code=422)
    return JSONResponse({
        "success":       True,
        "workflow":      result.workflow_json,
        "explanation":   result.explanation,
        "node_count":    len((result.workflow_json or {}).get("nodes", [])),
    })


def main() -> None:
    try:
        import uvicorn
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("uvicorn missing; run: pip install -r requirements.txt") from exc
    _start_background_worker()
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()


@app.post("/api/ai/chat")
async def api_ai_chat(request: Request):
    user = _current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": "messages is required"}, status_code=422)
    try:
        from ai_builder.ai_client import AIClient
        from ai_builder.chat_agent import ChatAgent
        client = AIClient()
        agent = ChatAgent(client)
        result = agent.chat(messages)
    except Exception as e:
        return JSONResponse({"error": f"AI builder unavailable: {e}"}, status_code=503)
    return JSONResponse(result)

