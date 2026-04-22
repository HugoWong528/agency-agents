"""
AI Auto Company — FastAPI automation platform.

Endpoints
---------
GET  /                     Full dashboard UI
GET  /health               Health check
GET  /agents               List all available agents
GET  /models               List allowed free models
GET  /context              Get platform settings (project context, auto-GitHub, self-improve)
POST /context              Update platform settings
POST /chat                 Single-agent chat (optional session continuity)
POST /task                 Synchronous multi-agent task workflow
POST /jobs                 Submit background job (async, returns job ID immediately)
GET  /jobs                 List all background jobs
GET  /jobs/{job_id}        Get job status / full result
DELETE /jobs/{job_id}      Cancel a pending job
POST /schedules            Create a recurring scheduled task
GET  /schedules            List all schedules
DELETE /schedules/{sid}    Remove a schedule
POST /github               GitHub API integration
POST /image                Generate image via Pollinations
POST /audio                Text-to-speech via Pollinations
"""

from __future__ import annotations

# Load .env file early so that config.py picks up the values
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)  # don't override already-set env vars
except ImportError:
    pass  # python-dotenv optional; env vars can be set another way

import json
import logging
import os
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import agents as agent_registry
import context_store
import github_tools
import job_queue
import task_scheduler
from config import (
    DEFAULT_MODEL,
    GITHUB_TOKEN,
    MODEL_ALLOWLIST,
    POLLINATIONS_API_KEYS,
    POLLINATIONS_BASE_URL,
    SERVER_API_KEY,
)
from llm import chat_completion, image_url, stream_chat_completion
from models import (
    AgentInfo,
    AgentListResponse,
    AudioRequest,
    ChatRequest,
    ChatResponse,
    ContextSettings,
    GitHubRequest,
    GitHubResponse,
    HealthResponse,
    ImageRequest,
    JobDetailResponse,
    JobInfo,
    JobSubmitRequest,
    Message,
    ScheduleCreateRequest,
    ScheduleInfo,
    TaskRequest,
    TaskResponse,
    TaskStep,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    loaded = agent_registry.load_agents()
    logger.info("Loaded %d agents.", len(loaded))
    if not POLLINATIONS_API_KEYS:
        logger.warning(
            "No POLLINATIONS_API_KEYS configured — requests will be anonymous."
        )
    if not GITHUB_TOKEN:
        logger.info("GITHUB_TOKEN not set — GitHub integration disabled.")

    # Start background job worker and scheduler
    worker_task = asyncio.create_task(job_queue.worker())
    task_scheduler.start()
    logger.info("Background worker and scheduler started.")

    yield  # server runs here

    # Cleanup
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    task_scheduler.stop()


app = FastAPI(
    title="AI Auto Company",
    description=(
        "Multi-role AI orchestrator powered by agency-agents repository "
        "and the Pollinations API (free models only)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)


def verify_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> None:
    """If SERVER_API_KEY is set, require a matching Bearer token."""
    if not SERVER_API_KEY:
        return  # auth disabled
    if credentials is None or credentials.credentials != SERVER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization token.",
        )


# ---------------------------------------------------------------------------
# In-memory session store  (session_id → list of messages)
# ---------------------------------------------------------------------------

from config import MAX_HISTORY_TURNS

_sessions: dict[str, list[dict[str, str]]] = defaultdict(list)


def _get_or_create_session(session_id: str | None) -> tuple[str, list[dict[str, str]]]:
    sid = session_id or str(uuid.uuid4())
    return sid, _sessions[sid]


def _append_and_trim(
    history: list[dict[str, str]], role: str, content: str
) -> None:
    history.append({"role": role, "content": content})
    # Keep at most MAX_HISTORY_TURNS pairs (user+assistant)
    max_msgs = MAX_HISTORY_TURNS * 2
    if len(history) > max_msgs:
        del history[: len(history) - max_msgs]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        agents_loaded=len(agent_registry.all_agents()),
        keys_configured=len(POLLINATIONS_API_KEYS),
        default_model=DEFAULT_MODEL,
    )


@app.get("/agents", response_model=AgentListResponse, tags=["Agents"])
async def list_agents(_: None = Depends(verify_auth)) -> AgentListResponse:
    items = [
        AgentInfo(
            slug=a.slug,
            name=a.name,
            description=a.description,
            category=a.category,
            emoji=a.emoji,
        )
        for a in agent_registry.all_agents()
    ]
    return AgentListResponse(agents=items, total=len(items))


@app.get("/models", tags=["System"])
async def list_models() -> dict[str, Any]:
    return {"models": MODEL_ALLOWLIST, "default": DEFAULT_MODEL}


# ---------------------------------------------------------------------------
# GET /context  POST /context  — platform settings
# ---------------------------------------------------------------------------


@app.get("/context", response_model=ContextSettings, tags=["System"])
async def get_context(_: None = Depends(verify_auth)) -> ContextSettings:
    """Return current platform settings (project context, auto-GitHub, self-improve)."""
    return ContextSettings(**context_store.as_dict())


@app.post("/context", response_model=ContextSettings, tags=["System"])
async def update_context(
    req: ContextSettings, _: None = Depends(verify_auth)
) -> ContextSettings:
    """Update platform settings. All fields are optional — omitted fields keep their current value."""
    return ContextSettings(**context_store.update(
        project_context=req.project_context,
        auto_github_repo=req.auto_github_repo,
        auto_github_branch=req.auto_github_branch,
        self_improve=req.self_improve,
    ).__dict__)


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=None, tags=["Chat"])
async def chat(
    req: ChatRequest, _: None = Depends(verify_auth)
) -> StreamingResponse | ChatResponse:
    # Resolve agent
    if req.agent:
        agent = agent_registry.get_agent(req.agent)
        if not agent:
            raise HTTPException(
                status_code=404,
                detail=f"Agent '{req.agent}' not found. "
                       f"Check GET /agents for valid slugs.",
            )
    else:
        agent = agent_registry.auto_select_agent(req.message)

    sid, history = _get_or_create_session(req.session_id)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": agent.system_prompt},
        *history,
        {"role": "user", "content": req.message},
    ]

    model = req.model or DEFAULT_MODEL

    if req.stream:
        async def event_stream() -> AsyncIterator[str]:
            yield f"data: {json.dumps({'event': 'agent', 'agent': agent.slug})}\n\n"
            full = []
            async for chunk in stream_chat_completion(messages, model=model):
                full.append(chunk)
                # Ensure proper SSE format: each line prefixed with "data: "
                yield f"data: {chunk}\n\n"
            # Persist assistant reply
            _append_and_trim(history, "user", req.message)
            _append_and_trim(history, "assistant", "".join(full))

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # Non-streaming
    try:
        result = await chat_completion(messages, model=model)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"LLM call failed: {exc}",
        ) from exc
    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage")

    _append_and_trim(history, "user", req.message)
    _append_and_trim(history, "assistant", content)

    return ChatResponse(
        session_id=sid,
        agent=agent.slug,
        model=model,
        content=content,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# POST /task  (multi-agent workflow)
# ---------------------------------------------------------------------------


@app.post("/task", response_model=TaskResponse, tags=["Tasks"])
async def run_task(req: TaskRequest, _: None = Depends(verify_auth)) -> TaskResponse:
    model = req.model or DEFAULT_MODEL
    steps: list[TaskStep] = []
    context = req.task  # accumulate outputs as rolling context

    if req.agents:
        # Explicit ordered list
        agent_slugs = req.agents
        selected_agents = []
        for slug in agent_slugs:
            a = agent_registry.get_agent(slug)
            if not a:
                raise HTTPException(404, detail=f"Agent '{slug}' not found.")
            selected_agents.append(a)
    else:
        # Auto-select: orchestrator first, then the best specialist
        orchestrator = agent_registry.get_agent("agents-orchestrator")
        specialist = agent_registry.auto_select_agent(req.task)
        selected_agents = list(
            dict.fromkeys(filter(None, [orchestrator, specialist]))
        )

    for agent in selected_agents:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": agent.system_prompt},
            {"role": "user", "content": context},
        ]
        try:
            result = await chat_completion(messages, model=model)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail=f"LLM call failed for agent {agent.slug}: {exc}"
            ) from exc
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage")

        steps.append(
            TaskStep(agent=agent.slug, content=content, model=model, usage=usage)
        )
        # Each agent's output becomes context for the next
        context = (
            f"Previous agent ({agent.name}) output:\n{content}\n\n"
            f"Original task: {req.task}"
        )

    # Summarise using the orchestrator (or last agent)
    summariser = agent_registry.get_agent("agents-orchestrator") or selected_agents[-1]
    summary_messages: list[dict[str, str]] = [
        {"role": "system", "content": summariser.system_prompt},
        {
            "role": "user",
            "content": (
                f"Summarise the following multi-agent workflow in 3-5 sentences:\n\n"
                + "\n\n---\n\n".join(
                    f"**{s.agent}**:\n{s.content}" for s in steps
                )
            ),
        },
    ]
    try:
        summary_result = await chat_completion(summary_messages, model=model)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Summary LLM call failed: {exc}") from exc
    summary = summary_result["choices"][0]["message"]["content"]

    return TaskResponse(steps=steps, summary=summary)


# ---------------------------------------------------------------------------
# Background Jobs  POST/GET /jobs  GET/DELETE /jobs/{job_id}
# ---------------------------------------------------------------------------


def _job_to_info(j: job_queue.Job) -> JobInfo:
    return JobInfo(
        id=j.id,
        task=j.task,
        agents=j.agents,
        model=j.model,
        status=j.status,
        created_at=j.created_at.isoformat(),
        started_at=j.started_at.isoformat() if j.started_at else None,
        finished_at=j.finished_at.isoformat() if j.finished_at else None,
        summary=j.result.get("summary") if j.result else None,
        error=j.error,
    )


@app.post("/jobs", response_model=JobInfo, status_code=202, tags=["Jobs"])
async def submit_job(
    req: JobSubmitRequest, _: None = Depends(verify_auth)
) -> JobInfo:
    """Submit a task as a background job. Returns immediately with a job ID."""
    j = job_queue.submit_job(req.task, agents=req.agents, model=req.model)
    return _job_to_info(j)


@app.get("/jobs", response_model=list[JobInfo], tags=["Jobs"])
async def list_jobs(_: None = Depends(verify_auth)) -> list[JobInfo]:
    """List all background jobs (most recent first)."""
    return [_job_to_info(j) for j in job_queue.all_jobs()]


@app.get("/jobs/{job_id}", response_model=JobDetailResponse, tags=["Jobs"])
async def get_job(job_id: str, _: None = Depends(verify_auth)) -> JobDetailResponse:
    """Get full detail for a single job, including step outputs."""
    j = job_queue.get_job(job_id)
    if not j:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    steps = []
    if j.result:
        steps = [
            TaskStep(
                agent=s["agent"],
                content=s["content"],
                model=s["model"],
                usage=s.get("usage"),
            )
            for s in j.result.get("steps", [])
        ]
    return JobDetailResponse(
        id=j.id,
        task=j.task,
        agents=j.agents,
        model=j.model,
        status=j.status,
        created_at=j.created_at.isoformat(),
        started_at=j.started_at.isoformat() if j.started_at else None,
        finished_at=j.finished_at.isoformat() if j.finished_at else None,
        summary=j.result.get("summary") if j.result else None,
        error=j.error,
        steps=steps,
    )


@app.delete("/jobs/{job_id}", tags=["Jobs"])
async def cancel_job(job_id: str, _: None = Depends(verify_auth)) -> dict[str, Any]:
    """Cancel a pending job."""
    ok = job_queue.cancel_job(job_id)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Job not found or not in 'pending' state — cannot cancel.",
        )
    return {"ok": True, "job_id": job_id}


# ---------------------------------------------------------------------------
# Schedules  POST/GET /schedules  DELETE /schedules/{sid}
# ---------------------------------------------------------------------------


@app.post("/schedules", response_model=ScheduleInfo, status_code=201, tags=["Schedules"])
async def create_schedule(
    req: ScheduleCreateRequest, _: None = Depends(verify_auth)
) -> ScheduleInfo:
    """Create a recurring scheduled task."""
    try:
        rec = task_scheduler.add_schedule(
            name=req.name,
            task=req.task,
            agents=req.agents,
            model=req.model,
            cron=req.cron,
            interval_minutes=req.interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ScheduleInfo(**rec)


@app.get("/schedules", response_model=list[ScheduleInfo], tags=["Schedules"])
async def list_schedules(_: None = Depends(verify_auth)) -> list[ScheduleInfo]:
    """List all active schedules."""
    return [ScheduleInfo(**s) for s in task_scheduler.all_schedules()]


@app.delete("/schedules/{schedule_id}", tags=["Schedules"])
async def delete_schedule(
    schedule_id: str, _: None = Depends(verify_auth)
) -> dict[str, Any]:
    """Remove a schedule by ID."""
    ok = task_scheduler.remove_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found.")
    return {"ok": True, "schedule_id": schedule_id}


# ---------------------------------------------------------------------------
# POST /github
# ---------------------------------------------------------------------------


@app.post("/github", response_model=GitHubResponse, tags=["GitHub"])
async def github_action(
    req: GitHubRequest, _: None = Depends(verify_auth)
) -> GitHubResponse:
    if not GITHUB_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="GitHub integration is disabled — set GITHUB_TOKEN.",
        )
    try:
        data = await github_tools.dispatch(req.action, req.repo, req.params)
        return GitHubResponse(ok=True, data=data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc


# ---------------------------------------------------------------------------
# POST /image
# ---------------------------------------------------------------------------


@app.post("/image", tags=["Media"])
async def generate_image(
    req: ImageRequest, _: None = Depends(verify_auth)
) -> dict[str, str]:
    model = req.model or "flux"  # flux is free
    url = image_url(req.prompt, model=model, width=req.width, height=req.height)
    return {"url": url, "model": model}


# ---------------------------------------------------------------------------
# POST /audio
# ---------------------------------------------------------------------------


@app.post("/audio", tags=["Media"])
async def generate_audio(
    req: AudioRequest, _: None = Depends(verify_auth)
) -> dict[str, str]:
    import urllib.parse

    encoded_text = urllib.parse.quote(req.text)
    url = (
        f"{POLLINATIONS_BASE_URL}/audio/{encoded_text}"
        f"?voice={req.voice}"
    )
    return {"url": url, "voice": req.voice}


# ---------------------------------------------------------------------------
# GET /   — Full dashboard UI
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AI Auto Company</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0d0d1a;color:#e0e0e0;display:flex;height:100vh;overflow:hidden}

/* Sidebar */
#sidebar{width:220px;min-width:220px;background:#13132a;border-right:1px solid #2a2a4a;display:flex;flex-direction:column;padding:16px 0}
#sidebar .logo{padding:0 20px 20px;border-bottom:1px solid #2a2a4a;margin-bottom:12px}
#sidebar .logo h1{font-size:1.1rem;color:#7dd3fc;font-weight:700}
#sidebar .logo p{font-size:.72rem;color:#888;margin-top:2px}
.nav-item{display:flex;align-items:center;gap:10px;padding:10px 20px;cursor:pointer;border-radius:0;font-size:.9rem;color:#aaa;border-left:3px solid transparent;transition:all .15s}
.nav-item:hover{background:#1e1e3a;color:#e0e0e0}
.nav-item.active{background:#1e1e3a;color:#7dd3fc;border-left-color:#7dd3fc}
.nav-item .icon{font-size:1.1rem;width:22px;text-align:center}
#sidebar .footer{margin-top:auto;padding:12px 20px;font-size:.72rem;color:#555;border-top:1px solid #2a2a4a}
#sidebar .footer a{color:#6366f1;text-decoration:none}

/* Main content */
#main{flex:1;overflow-y:auto;padding:28px 32px}
.section{display:none}
.section.active{display:block}

h2{font-size:1.3rem;font-weight:700;color:#a5b4fc;margin-bottom:16px}
h3{font-size:1rem;font-weight:600;color:#c4b5fd;margin-bottom:10px}

/* Cards / panels */
.card{background:#13132a;border:1px solid #2a2a4a;border-radius:10px;padding:20px;margin-bottom:16px}
.stats-row{display:flex;gap:16px;margin-bottom:20px;flex-wrap:wrap}
.stat-card{flex:1;min-width:130px;background:#13132a;border:1px solid #2a2a4a;border-radius:10px;padding:16px;text-align:center}
.stat-card .num{font-size:2rem;font-weight:700;color:#7dd3fc}
.stat-card .label{font-size:.8rem;color:#888;margin-top:4px}

/* Forms */
input,select,textarea{background:#1e1e3a;border:1px solid #3a3a5a;color:#e0e0e0;padding:9px 12px;border-radius:7px;font-size:.9rem;width:100%;outline:none;transition:border .15s}
input:focus,select:focus,textarea:focus{border-color:#6366f1}
select option{background:#1e1e3a}
.form-row{display:flex;gap:10px;margin-bottom:10px}
.form-row>*{flex:1}
label{display:block;font-size:.8rem;color:#888;margin-bottom:4px}
.form-group{margin-bottom:12px}
textarea{resize:vertical;min-height:80px}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:9px 18px;border:none;border-radius:7px;cursor:pointer;font-size:.88rem;font-weight:600;transition:all .15s}
.btn-primary{background:#6366f1;color:#fff}
.btn-primary:hover{background:#818cf8}
.btn-danger{background:#dc2626;color:#fff;padding:5px 12px;font-size:.8rem}
.btn-danger:hover{background:#ef4444}
.btn-sm{padding:5px 12px;font-size:.8rem}
.btn-ghost{background:transparent;color:#6366f1;border:1px solid #6366f1}
.btn-ghost:hover{background:#6366f120}

/* Job list */
.job-row{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:8px;background:#0d0d1a;border:1px solid #2a2a4a;margin-bottom:8px;cursor:pointer;transition:border .15s}
.job-row:hover{border-color:#6366f1}
.job-row .task{flex:1;font-size:.88rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#e0e0e0}
.job-row .meta{font-size:.75rem;color:#777}
.badge{display:inline-block;padding:3px 8px;border-radius:4px;font-size:.75rem;font-weight:600}
.badge-pending{background:#374151;color:#9ca3af}
.badge-running{background:#1e3a5f;color:#60a5fa;animation:pulse 1.5s infinite}
.badge-done{background:#14532d;color:#4ade80}
.badge-error{background:#450a0a;color:#f87171}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}

/* Chat */
#chat-messages{background:#0d0d1a;border:1px solid #2a2a4a;border-radius:10px;height:380px;overflow-y:auto;padding:16px;margin-bottom:12px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:80%;padding:10px 14px;border-radius:10px;font-size:.88rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
.msg.user{background:#3b3bdb;color:#fff;align-self:flex-end;border-bottom-right-radius:2px}
.msg.assistant{background:#1e1e3a;color:#e0e0e0;align-self:flex-start;border-bottom-left-radius:2px}
.msg.system{background:transparent;color:#666;align-self:center;font-size:.78rem;font-style:italic}
.chat-input-row{display:flex;gap:8px}
.chat-input-row textarea{flex:1;min-height:48px;max-height:120px}

/* Agent grid */
.agent-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.agent-card{background:#13132a;border:1px solid #2a2a4a;border-radius:8px;padding:14px;transition:border .15s}
.agent-card:hover{border-color:#6366f1}
.agent-card .emoji{font-size:1.4rem;margin-bottom:6px}
.agent-card .name{font-size:.9rem;font-weight:600;color:#c4b5fd}
.agent-card .cat{font-size:.73rem;color:#6366f1;background:#1e1e3a;padding:2px 7px;border-radius:3px;display:inline-block;margin:4px 0}
.agent-card .desc{font-size:.78rem;color:#888;margin-top:4px;line-height:1.4}

/* Modal */
#modal-overlay{display:none;position:fixed;inset:0;background:#00000099;z-index:100;align-items:center;justify-content:center}
#modal-overlay.open{display:flex}
#modal{background:#13132a;border:1px solid #3a3a5a;border-radius:12px;padding:28px;max-width:680px;width:90%;max-height:85vh;overflow-y:auto}
#modal h3{color:#7dd3fc;margin-bottom:16px;font-size:1.1rem}
.step-block{background:#0d0d1a;border:1px solid #2a2a4a;border-radius:8px;padding:14px;margin-bottom:12px}
.step-block .agent-tag{font-size:.78rem;color:#6366f1;font-weight:600;margin-bottom:6px}
.step-block .content{font-size:.85rem;white-space:pre-wrap;color:#d0d0e0;line-height:1.55}
.summary-block{background:#1e3a1a;border:1px solid #2d5a2d;border-radius:8px;padding:14px;margin-bottom:16px}
.summary-block p{font-size:.88rem;color:#a0f0a0;line-height:1.55}
.close-btn{float:right;background:transparent;border:none;color:#888;cursor:pointer;font-size:1.2rem;line-height:1}

/* Misc */
.empty{text-align:center;color:#555;padding:40px 0;font-size:.9rem}
.error-msg{color:#f87171;font-size:.85rem;margin-top:6px}
.success-msg{color:#4ade80;font-size:.85rem;margin-top:6px}
hr{border:none;border-top:1px solid #2a2a4a;margin:20px 0}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:#13132a}
::-webkit-scrollbar-thumb{background:#3a3a5a;border-radius:3px}
</style>
</head>
<body>

<!-- Sidebar -->
<div id="sidebar">
  <div class="logo">
    <h1>🤖 AI Auto Company</h1>
    <p>Automation Platform</p>
  </div>
  <div class="nav-item active" onclick="showSection('dashboard')" id="nav-dashboard">
    <span class="icon">📊</span> Dashboard
  </div>
  <div class="nav-item" onclick="showSection('jobs')" id="nav-jobs">
    <span class="icon">⚙️</span> Jobs
  </div>
  <div class="nav-item" onclick="showSection('chat')" id="nav-chat">
    <span class="icon">💬</span> Chat
  </div>
  <div class="nav-item" onclick="showSection('schedules')" id="nav-schedules">
    <span class="icon">🕐</span> Schedules
  </div>
  <div class="nav-item" onclick="showSection('settings')" id="nav-settings">
    <span class="icon">🔧</span> Settings
  </div>
  <div class="nav-item" onclick="showSection('agents')" id="nav-agents">
    <span class="icon">🧠</span> Agents
  </div>
  <div class="footer">
    <a href="/docs">API Docs</a> &nbsp;·&nbsp; <a href="/redoc">ReDoc</a><br/>
    Powered by <a href="https://pollinations.ai" target="_blank">Pollinations</a>
  </div>
</div>

<!-- Main -->
<div id="main">

  <!-- Dashboard -->
  <div class="section active" id="section-dashboard">
    <h2>📊 Dashboard</h2>
    <div class="stats-row" id="stats-row">
      <div class="stat-card"><div class="num" id="stat-agents">…</div><div class="label">Agents Loaded</div></div>
      <div class="stat-card"><div class="num" id="stat-jobs">0</div><div class="label">Total Jobs</div></div>
      <div class="stat-card"><div class="num" id="stat-running">0</div><div class="label">Running</div></div>
      <div class="stat-card"><div class="num" id="stat-done">0</div><div class="label">Completed</div></div>
      <div class="stat-card"><div class="num" id="stat-schedules">0</div><div class="label">Schedules</div></div>
    </div>

    <div class="card">
      <h3>🚀 Quick Job Submit</h3>
      <div class="form-group">
        <label>Task description</label>
        <textarea id="dash-task" rows="3" placeholder="Describe what you want the AI to do automatically…"></textarea>
      </div>
      <div class="form-row">
        <div>
          <label>Agent (optional)</label>
          <input id="dash-agent" placeholder="e.g. ai-engineer (blank = auto)"/>
        </div>
        <div>
          <label>Model</label>
          <select id="dash-model">
            <option value="">Default</option>
            MODELS_OPTIONS
          </select>
        </div>
      </div>
      <button class="btn btn-primary" onclick="dashSubmitJob()">▶ Run Job Now</button>
      <div id="dash-msg"></div>
    </div>

    <div class="card">
      <h3>🕐 Recent Jobs</h3>
      <div id="dash-recent-jobs"><div class="empty">No jobs yet.</div></div>
    </div>
  </div>

  <!-- Jobs -->
  <div class="section" id="section-jobs">
    <h2>⚙️ Background Jobs</h2>
    <div class="card">
      <h3>Submit New Job</h3>
      <div class="form-group">
        <label>Task</label>
        <textarea id="job-task" rows="3" placeholder="What should the agents do?"></textarea>
      </div>
      <div class="form-row">
        <div>
          <label>Agents (comma-separated slugs, optional)</label>
          <input id="job-agents" placeholder="e.g. ai-engineer,code-reviewer"/>
        </div>
        <div>
          <label>Model</label>
          <select id="job-model">
            <option value="">Default</option>
            MODELS_OPTIONS
          </select>
        </div>
      </div>
      <button class="btn btn-primary" onclick="submitJob()">▶ Submit Job</button>
      <div id="job-msg"></div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h3 style="margin:0">Job Queue</h3>
        <button class="btn btn-ghost btn-sm" onclick="loadJobs()">↻ Refresh</button>
      </div>
      <div id="jobs-list"><div class="empty">No jobs yet.</div></div>
    </div>
  </div>

  <!-- Chat -->
  <div class="section" id="section-chat">
    <h2>💬 Chat with Agents</h2>
    <div class="card" style="padding:0;overflow:hidden">
      <div style="padding:16px 20px;border-bottom:1px solid #2a2a4a;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <div style="flex:1;min-width:160px">
          <label>Agent (blank = auto-select)</label>
          <input id="chat-agent" placeholder="e.g. frontend-developer"/>
        </div>
        <div style="flex:1;min-width:140px">
          <label>Model</label>
          <select id="chat-model">
            <option value="">Default</option>
            MODELS_OPTIONS
          </select>
        </div>
        <div style="align-self:flex-end">
          <button class="btn btn-ghost btn-sm" onclick="clearChat()">🗑 Clear</button>
        </div>
      </div>
      <div style="padding:16px 20px">
        <div id="chat-messages"><div class="msg system">Start a conversation…</div></div>
        <div class="chat-input-row">
          <textarea id="chat-input" rows="2" placeholder="Type a message… (Shift+Enter for new line, Enter to send)"></textarea>
          <button class="btn btn-primary" onclick="sendChat()" style="align-self:flex-end;white-space:nowrap">Send ▶</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Schedules -->
  <div class="section" id="section-schedules">
    <h2>🕐 Scheduled Tasks</h2>
    <div class="card">
      <h3>Create New Schedule</h3>
      <div class="form-row">
        <div>
          <label>Schedule name</label>
          <input id="sched-name" placeholder="e.g. Daily SEO Report"/>
        </div>
        <div>
          <label>Model (optional)</label>
          <select id="sched-model">
            <option value="">Default</option>
            MODELS_OPTIONS
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Task description</label>
        <textarea id="sched-task" rows="3" placeholder="What should run automatically?"></textarea>
      </div>
      <div class="form-row">
        <div>
          <label>Cron expression (5 fields: min hr day mon weekday)</label>
          <input id="sched-cron" placeholder="e.g. 0 9 * * 1-5  (weekdays 9am)"/>
        </div>
        <div>
          <label>— OR — Interval (minutes)</label>
          <input id="sched-interval" type="number" min="1" placeholder="e.g. 60"/>
        </div>
      </div>
      <button class="btn btn-primary" onclick="createSchedule()">+ Create Schedule</button>
      <div id="sched-msg"></div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h3 style="margin:0">Active Schedules</h3>
        <button class="btn btn-ghost btn-sm" onclick="loadSchedules()">↻ Refresh</button>
      </div>
      <div id="schedules-list"><div class="empty">No schedules yet.</div></div>
    </div>
  </div>

  <!-- Agents -->
  <div class="section" id="section-agents">
    <h2>🧠 Available Agents</h2>
    <div style="margin-bottom:14px">
      <input id="agent-search" placeholder="🔍 Search agents by name, slug, or category…" oninput="filterAgents()"/>
    </div>
    <div id="agent-count" style="font-size:.8rem;color:#666;margin-bottom:12px"></div>
    <div class="agent-grid" id="agent-grid"><div class="empty">Loading agents…</div></div>
  </div>

  <!-- Settings -->
  <div class="section" id="section-settings">
    <h2>🔧 Platform Settings</h2>
    <p style="font-size:.85rem;color:#888;margin-bottom:20px">
      These settings apply to <strong>every</strong> job and are saved server-side for the current session.
    </p>

    <div class="card">
      <h3>📋 Project Context</h3>
      <p style="font-size:.8rem;color:#888;margin-bottom:10px">
        Describe your project, tech stack, goals, and any conventions. This text is automatically
        prepended to every agent prompt so the AI always knows what it's working on.
      </p>
      <div class="form-group">
        <label>Project context (markdown supported)</label>
        <textarea id="ctx-project" rows="6" placeholder="e.g. We are building a SaaS product called Acme using FastAPI + React + PostgreSQL. Always use TypeScript for frontend code. Our GitHub repo is acme/my-project."></textarea>
      </div>
    </div>

    <div class="card">
      <h3>🤖 Self-Improvement</h3>
      <p style="font-size:.8rem;color:#888;margin-bottom:12px">
        When enabled, each completed job automatically runs a <strong>critic review pass</strong> followed
        by a <strong>specialist refine pass</strong> to improve the output quality before marking the job done.
      </p>
      <div style="display:flex;align-items:center;gap:12px">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:#e0e0e0;font-size:.9rem">
          <input type="checkbox" id="ctx-self-improve" style="width:18px;height:18px;cursor:pointer;accent-color:#6366f1"/>
          Enable automatic self-improvement on every job
        </label>
      </div>
    </div>

    <div class="card">
      <h3>🐙 Auto-Upload to GitHub</h3>
      <p style="font-size:.8rem;color:#888;margin-bottom:12px">
        When a GitHub Token is configured server-side and a repo is set here, every completed job
        will <strong>automatically create a GitHub Issue</strong> in that repo with the full result.
      </p>
      <div class="form-row">
        <div>
          <label>Target repository (owner/repo)</label>
          <input id="ctx-github-repo" placeholder="e.g. acme/my-project"/>
        </div>
        <div>
          <label>Branch (for future file commits)</label>
          <input id="ctx-github-branch" placeholder="main"/>
        </div>
      </div>
      <p style="font-size:.75rem;color:#555;margin-top:6px">
        💡 Requires <code>GITHUB_TOKEN</code> to be set as an environment variable on the server.
      </p>
    </div>

    <button class="btn btn-primary" onclick="saveContext()" style="margin-top:4px">💾 Save Settings</button>
    <div id="ctx-msg" style="margin-top:10px"></div>
  </div>
</div>

<!-- Job Detail Modal -->
<div id="modal-overlay" onclick="closeModal(event)">
  <div id="modal">
    <button class="close-btn" onclick="closeModalDirect()">✕</button>
    <h3 id="modal-title">Job Details</h3>
    <div id="modal-body"></div>
  </div>
</div>

<script>
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function getToken(){return localStorage.getItem('api_key')||'';}
function authHeaders(){
  const h={'Content-Type':'application/json'};
  const k=getToken();if(k)h['Authorization']='Bearer '+k;
  return h;
}
(function(){
  const k=new URLSearchParams(location.search).get('key');
  if(k)localStorage.setItem('api_key',k);
})();

async function apiFetch(url,opts){
  opts=opts||{};
  opts.headers=Object.assign(authHeaders(),opts.headers||{});
  const r=await fetch(url,opts);
  const d=await r.json();
  if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
  return d;
}

function statusBadge(s){
  return `<span class="badge badge-${s}">${s}</span>`;
}
function relTime(iso){
  if(!iso)return '';
  const d=new Date(iso),n=new Date();
  const sec=Math.round((n-d)/1000);
  if(sec<60)return sec+'s ago';
  if(sec<3600)return Math.round(sec/60)+'m ago';
  return Math.round(sec/3600)+'h ago';
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
function showSection(name){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  document.getElementById('section-'+name).classList.add('active');
  document.getElementById('nav-'+name).classList.add('active');
  if(name==='dashboard')refreshDashboard();
  if(name==='jobs')loadJobs();
  if(name==='agents')loadAgents();
  if(name==='schedules')loadSchedules();
  if(name==='settings')loadContext();
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
let _dashPoll=null;
async function refreshDashboard(){
  try{
    const [health,jobs,scheds]=await Promise.all([
      apiFetch('/health'),
      apiFetch('/jobs'),
      apiFetch('/schedules'),
    ]);
    document.getElementById('stat-agents').textContent=health.agents_loaded;
    document.getElementById('stat-jobs').textContent=jobs.length;
    document.getElementById('stat-running').textContent=jobs.filter(j=>j.status==='running').length;
    document.getElementById('stat-done').textContent=jobs.filter(j=>j.status==='done').length;
    document.getElementById('stat-schedules').textContent=scheds.length;
    renderRecentJobs(jobs.slice(0,5));
  }catch(e){console.warn('Dashboard refresh error:',e);}
}

function renderRecentJobs(jobs){
  const el=document.getElementById('dash-recent-jobs');
  if(!jobs.length){el.innerHTML='<div class="empty">No jobs yet.</div>';return;}
  el.innerHTML=jobs.map(j=>`
    <div class="job-row" onclick="openJobModal('${j.id}')">
      <span style="font-size:1rem">${j.status==='done'?'✅':j.status==='error'?'❌':j.status==='running'?'⚙️':'🕐'}</span>
      <span class="task" title="${escHtml(j.task)}">${escHtml(j.task)}</span>
      ${statusBadge(j.status)}
      <span class="meta">${relTime(j.created_at)}</span>
    </div>`).join('');
}

async function dashSubmitJob(){
  const msg=document.getElementById('dash-msg');
  const task=document.getElementById('dash-task').value.trim();
  if(!task){msg.innerHTML='<span class="error-msg">⚠ Please enter a task.</span>';return;}
  const agent=document.getElementById('dash-agent').value.trim();
  const model=document.getElementById('dash-model').value;
  msg.innerHTML='<span style="color:#60a5fa">Submitting…</span>';
  try{
    const j=await apiFetch('/jobs',{method:'POST',body:JSON.stringify({
      task,agents:agent?agent.split(',').map(s=>s.trim()).filter(Boolean):null,model:model||null
    })});
    msg.innerHTML=`<span class="success-msg">✅ Job submitted! ID: <code>${j.id}</code></span>`;
    refreshDashboard();
  }catch(e){msg.innerHTML=`<span class="error-msg">❌ ${escHtml(e.message)}</span>`;}
}

// Auto-refresh dashboard every 5s when visible
setInterval(()=>{
  if(document.getElementById('section-dashboard').classList.contains('active'))refreshDashboard();
},5000);
refreshDashboard();

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
let _jobsPoll=null;
async function loadJobs(){
  const el=document.getElementById('jobs-list');
  try{
    const jobs=await apiFetch('/jobs');
    if(!jobs.length){el.innerHTML='<div class="empty">No jobs yet.</div>';return;}
    el.innerHTML=jobs.map(j=>`
      <div class="job-row" onclick="openJobModal('${j.id}')">
        <span style="font-size:1rem">${j.status==='done'?'✅':j.status==='error'?'❌':j.status==='running'?'⚙️':'🕐'}</span>
        <span class="task" title="${escHtml(j.task)}">${escHtml(j.task.substring(0,80))}${j.task.length>80?'…':''}</span>
        ${statusBadge(j.status)}
        <span class="meta">${relTime(j.created_at)}</span>
        ${j.status==='pending'?`<button class="btn btn-danger" onclick="cancelJob(event,'${j.id}')">✕</button>`:''}
      </div>`).join('');
  }catch(e){el.innerHTML=`<div class="empty" style="color:#f87171">Error: ${escHtml(e.message)}</div>`;}
}
// Poll jobs every 3s
setInterval(()=>{
  if(document.getElementById('section-jobs').classList.contains('active'))loadJobs();
},3000);

async function submitJob(){
  const msg=document.getElementById('job-msg');
  const task=document.getElementById('job-task').value.trim();
  if(!task){msg.innerHTML='<span class="error-msg">⚠ Please enter a task.</span>';return;}
  const agentsRaw=document.getElementById('job-agents').value.trim();
  const model=document.getElementById('job-model').value;
  msg.innerHTML='<span style="color:#60a5fa">Submitting…</span>';
  try{
    const j=await apiFetch('/jobs',{method:'POST',body:JSON.stringify({
      task,
      agents:agentsRaw?agentsRaw.split(',').map(s=>s.trim()).filter(Boolean):null,
      model:model||null,
    })});
    msg.innerHTML=`<span class="success-msg">✅ Submitted! Job ID: <code>${j.id}</code></span>`;
    document.getElementById('job-task').value='';
    loadJobs();
  }catch(e){msg.innerHTML=`<span class="error-msg">❌ ${escHtml(e.message)}</span>`;}
}

async function cancelJob(ev,id){
  ev.stopPropagation();
  try{
    await apiFetch('/jobs/'+id,{method:'DELETE'});
    loadJobs();
  }catch(e){alert('Cancel failed: '+e.message);}
}
async function cancelJobById(id){
  try{
    await apiFetch('/jobs/'+id,{method:'DELETE'});
    closeModalDirect();
    loadJobs();
  }catch(e){alert('Cancel failed: '+e.message);}
}

// ---------------------------------------------------------------------------
// Job Modal
// ---------------------------------------------------------------------------
async function openJobModal(id){
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('modal-body').innerHTML='<div style="text-align:center;padding:30px;color:#888">Loading…</div>';
  try{
    const j=await apiFetch('/jobs/'+id);
    document.getElementById('modal-title').textContent='Job: '+j.task.substring(0,60)+(j.task.length>60?'…':'');
    let html=`<div style="display:flex;gap:8px;align-items:center;margin-bottom:16px;flex-wrap:wrap">
      ${statusBadge(j.status)}
      <span style="font-size:.8rem;color:#888">Created: ${new Date(j.created_at).toLocaleString()}</span>
      ${j.started_at?`<span style="font-size:.8rem;color:#888">Started: ${new Date(j.started_at).toLocaleString()}</span>`:''}
      ${j.finished_at?`<span style="font-size:.8rem;color:#888">Finished: ${new Date(j.finished_at).toLocaleString()}</span>`:''}
    </div>`;
    if(j.error) html+=`<div class="error-msg" style="margin-bottom:14px">❌ ${escHtml(j.error)}</div>`;
    if(j.summary) html+=`<div class="summary-block"><strong style="color:#86efac;font-size:.85rem">📝 Summary</strong><p style="margin-top:6px">${escHtml(j.summary)}</p></div>`;
    if(j.steps&&j.steps.length){
      html+='<h3 style="margin-bottom:10px;color:#a5b4fc">Agent Steps</h3>';
      html+=j.steps.map(s=>`<div class="step-block"><div class="agent-tag">🤖 ${escHtml(s.agent)} · ${escHtml(s.model)}</div><div class="content">${escHtml(s.content)}</div></div>`).join('');
    }
    if(j.status==='pending')html+=`<button class="btn btn-danger" onclick="cancelJobById('${j.id}')">✕ Cancel Job</button>`;
    document.getElementById('modal-body').innerHTML=html;
  }catch(e){document.getElementById('modal-body').innerHTML=`<div style="color:#f87171">${escHtml(e.message)}</div>`;}
}
function closeModal(e){if(e.target===document.getElementById('modal-overlay'))closeModalDirect();}
function closeModalDirect(){document.getElementById('modal-overlay').classList.remove('open');}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------
let chatSession=null;
function appendMsg(role,text){
  const box=document.getElementById('chat-messages');
  const div=document.createElement('div');
  div.className='msg '+role;
  div.textContent=text;
  box.appendChild(div);
  box.scrollTop=box.scrollHeight;
}
function clearChat(){
  document.getElementById('chat-messages').innerHTML='<div class="msg system">Conversation cleared.</div>';
  chatSession=null;
}
async function sendChat(){
  const input=document.getElementById('chat-input');
  const msg=input.value.trim();
  if(!msg)return;
  input.value='';
  appendMsg('user',msg);
  const agent=document.getElementById('chat-agent').value.trim()||null;
  const model=document.getElementById('chat-model').value||null;
  const thinking=document.createElement('div');
  thinking.className='msg assistant';thinking.textContent='⏳ Thinking…';
  document.getElementById('chat-messages').appendChild(thinking);
  document.getElementById('chat-messages').scrollTop=9999;
  try{
    const d=await apiFetch('/chat',{method:'POST',body:JSON.stringify({
      message:msg,agent,model,session_id:chatSession
    })});
    chatSession=d.session_id;
    thinking.textContent=d.content;
    thinking.title='Agent: '+d.agent+' · Model: '+d.model;
  }catch(e){
    thinking.textContent='❌ '+e.message;
    thinking.style.color='#f87171';
  }
}
document.addEventListener('keydown',function(e){
  if(document.activeElement===document.getElementById('chat-input')&&e.key==='Enter'&&!e.shiftKey){
    e.preventDefault();sendChat();
  }
});

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------
async function loadSchedules(){
  const el=document.getElementById('schedules-list');
  try{
    const scheds=await apiFetch('/schedules');
    if(!scheds.length){el.innerHTML='<div class="empty">No schedules. Create one above.</div>';return;}
    el.innerHTML=scheds.map(s=>`
      <div class="job-row">
        <span style="font-size:1.1rem">🕐</span>
        <div style="flex:1">
          <div style="font-weight:600;font-size:.9rem;color:#c4b5fd">${escHtml(s.name)}</div>
          <div style="font-size:.8rem;color:#888;margin-top:2px">${escHtml(s.task.substring(0,70))}${s.task.length>70?'…':''}</div>
          <div style="font-size:.75rem;color:#555;margin-top:2px">
            ${s.cron?'cron: <code>'+escHtml(s.cron)+'</code>':s.interval_minutes+'min interval'}
            ${s.next_run?' · next: '+new Date(s.next_run).toLocaleString():''}
          </div>
        </div>
        <button class="btn btn-danger" onclick="deleteSchedule('${s.id}')">✕ Remove</button>
      </div>`).join('');
  }catch(e){el.innerHTML=`<div class="empty" style="color:#f87171">Error: ${escHtml(e.message)}</div>`;}
}
async function createSchedule(){
  const msg=document.getElementById('sched-msg');
  const name=document.getElementById('sched-name').value.trim();
  const task=document.getElementById('sched-task').value.trim();
  const cron=document.getElementById('sched-cron').value.trim()||null;
  const iv=parseInt(document.getElementById('sched-interval').value)||null;
  const model=document.getElementById('sched-model').value||null;
  if(!name||!task){msg.innerHTML='<span class="error-msg">⚠ Name and task are required.</span>';return;}
  if(!cron&&!iv){msg.innerHTML='<span class="error-msg">⚠ Provide cron or interval.</span>';return;}
  msg.innerHTML='<span style="color:#60a5fa">Creating…</span>';
  try{
    await apiFetch('/schedules',{method:'POST',body:JSON.stringify({name,task,cron,interval_minutes:iv,model})});
    msg.innerHTML='<span class="success-msg">✅ Schedule created!</span>';
    document.getElementById('sched-name').value='';
    document.getElementById('sched-task').value='';
    document.getElementById('sched-cron').value='';
    document.getElementById('sched-interval').value='';
    loadSchedules();
  }catch(e){msg.innerHTML=`<span class="error-msg">❌ ${escHtml(e.message)}</span>`;}
}
async function deleteSchedule(id){
  if(!confirm('Remove this schedule?'))return;
  try{await apiFetch('/schedules/'+id,{method:'DELETE'});loadSchedules();}
  catch(e){alert('Error: '+e.message);}
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------
let _allAgents=[];
async function loadAgents(){
  const grid=document.getElementById('agent-grid');
  try{
    const d=await apiFetch('/agents');
    _allAgents=d.agents;
    renderAgents(_allAgents);
  }catch(e){grid.innerHTML=`<div class="empty" style="color:#f87171">Error: ${escHtml(e.message)}</div>`;}
}
function renderAgents(list){
  const grid=document.getElementById('agent-grid');
  document.getElementById('agent-count').textContent=list.length+' agent'+(list.length!==1?'s':'');
  if(!list.length){grid.innerHTML='<div class="empty">No matches.</div>';return;}
  grid.innerHTML=list.map(a=>`
    <div class="agent-card">
      <div class="emoji">${a.emoji||'🤖'}</div>
      <div class="name">${escHtml(a.name)}</div>
      <div><span class="cat">${escHtml(a.category)}</span></div>
      <div class="desc">${escHtml(a.description||'')}</div>
      <div style="margin-top:8px"><code style="font-size:.75rem;color:#6366f1">${escHtml(a.slug)}</code></div>
    </div>`).join('');
}
function filterAgents(){
  const q=document.getElementById('agent-search').value.toLowerCase();
  if(!q){renderAgents(_allAgents);return;}
  renderAgents(_allAgents.filter(a=>
    a.slug.includes(q)||a.name.toLowerCase().includes(q)||
    (a.description||'').toLowerCase().includes(q)||
    a.category.toLowerCase().includes(q)
  ));
}

// ---------------------------------------------------------------------------
// Settings (platform context)
// ---------------------------------------------------------------------------
async function loadContext(){
  try{
    const c=await apiFetch('/context');
    document.getElementById('ctx-project').value=c.project_context||'';
    document.getElementById('ctx-self-improve').checked=c.self_improve!==false;
    document.getElementById('ctx-github-repo').value=c.auto_github_repo||'';
    document.getElementById('ctx-github-branch').value=c.auto_github_branch||'main';
  }catch(e){console.warn('loadContext error:',e);}
}
async function saveContext(){
  const msg=document.getElementById('ctx-msg');
  msg.innerHTML='<span style="color:#60a5fa">Saving…</span>';
  try{
    await apiFetch('/context',{method:'POST',body:JSON.stringify({
      project_context:document.getElementById('ctx-project').value,
      self_improve:document.getElementById('ctx-self-improve').checked,
      auto_github_repo:document.getElementById('ctx-github-repo').value.trim(),
      auto_github_branch:document.getElementById('ctx-github-branch').value.trim()||'main',
    })});
    msg.innerHTML='<span class="success-msg">✅ Settings saved! All future jobs will use these settings.</span>';
  }catch(e){msg.innerHTML=`<span class="error-msg">❌ ${escHtml(e.message)}</span>`;}
}
// Load context on startup (for background use)
(async()=>{try{await loadContext();}catch(_){} })();

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------
function escHtml(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse, tags=["UI"])
async def index() -> HTMLResponse:
    options = "\n    ".join(
        f'<option value="{m}">{m}</option>' for m in MODEL_ALLOWLIST
    )
    html = _HTML.replace("MODELS_OPTIONS", options)
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Entry point for direct python execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    from config import HOST, PORT

    uvicorn.run("main:app", host=HOST, port=PORT, reload=False)
