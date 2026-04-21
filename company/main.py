"""
AI Auto Company — FastAPI orchestrator server.

Endpoints
---------
GET  /                     HTML landing page / simple UI
GET  /health               Health check
GET  /agents               List all available agents
POST /chat                 Single-agent chat (optional session continuity)
POST /task                 Multi-agent task workflow
POST /github               GitHub API integration
POST /image                Generate image via Pollinations
POST /audio                Text-to-speech via Pollinations
GET  /models               List allowed free models
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
import github_tools
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
    GitHubRequest,
    GitHubResponse,
    HealthResponse,
    ImageRequest,
    Message,
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
    yield  # server runs here


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
# GET /   — simple HTML UI
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>AI Auto Company</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;background:#0f0f0f;color:#e0e0e0}
  h1{color:#7dd3fc}h2{color:#a5b4fc;font-size:1rem;margin-top:2rem}
  input,select,textarea{width:100%;background:#1e1e2e;border:1px solid #444;color:#e0e0e0;padding:8px;border-radius:6px;font-size:.95rem;box-sizing:border-box}
  button{background:#6366f1;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;margin-top:8px}
  button:hover{background:#818cf8}
  #output{background:#1a1a2e;border:1px solid #333;border-radius:8px;padding:16px;min-height:120px;white-space:pre-wrap;font-size:.9rem;margin-top:12px}
  .row{display:flex;gap:8px}
  .row input,.row select{flex:1}
  a{color:#7dd3fc}
  code{background:#2a2a3e;padding:2px 6px;border-radius:4px}
</style>
</head>
<body>
<h1>🤖 AI Auto Company</h1>
<p>Powered by <a href="https://github.com/msitarzewski/agency-agents" target="_blank">agency-agents</a>
   + <a href="https://pollinations.ai" target="_blank">Pollinations API</a> (free models only)</p>

<h2>📋 Quick Chat</h2>
<div class="row">
  <input id="agent" placeholder="Agent slug (blank = auto-select)"/>
  <select id="model">
    <option value="">Default model</option>
    MODELS_OPTIONS
  </select>
</div>
<textarea id="message" rows="4" placeholder="Type your task or message…"></textarea>
<br/>
<button onclick="sendChat()">Send ▶</button>

<h2>🔗 Multi-Agent Task</h2>
<textarea id="task" rows="3" placeholder="Describe a task — the orchestrator will pick agents…"></textarea>
<br/>
<button onclick="sendTask()">Run Task ▶</button>

<div id="output">Responses appear here…</div>

<h2>📖 API Docs</h2>
<p><a href="/docs">/docs</a> — Swagger UI &nbsp;|&nbsp; <a href="/redoc">/redoc</a> — ReDoc</p>
<p><a href="/agents">/agents</a> — JSON list of all agents &nbsp;|&nbsp; <a href="/models">/models</a> — Allowed models</p>

<script>
const out = document.getElementById('output');
function showLoading(){out.textContent='⏳ Waiting for response…';}
function getToken(){return localStorage.getItem('api_key') || '';}

// Prompt for server key once if needed
(function(){
  const k = new URLSearchParams(location.search).get('key');
  if(k) localStorage.setItem('api_key', k);
})();

async function sendChat(){
  const body = {
    message: document.getElementById('message').value.trim(),
    agent: document.getElementById('agent').value.trim() || null,
    model: document.getElementById('model').value || null,
  };
  if(!body.message){out.textContent='⚠ Please enter a message.'; return;}
  showLoading();
  const headers = {'Content-Type':'application/json'};
  const key = getToken(); if(key) headers['Authorization']='Bearer '+key;
  try{
    const r = await fetch('/chat', {method:'POST', headers, body:JSON.stringify(body)});
    const d = await r.json();
    if(!r.ok){out.textContent='Error '+r.status+': '+(d.detail||JSON.stringify(d)); return;}
    out.textContent = '[Agent: '+d.agent+' | Model: '+d.model+']\n\n'+d.content;
  }catch(e){out.textContent='Fetch error: '+e;}
}

async function sendTask(){
  const body = {task: document.getElementById('task').value.trim()};
  if(!body.task){out.textContent='⚠ Please describe a task.'; return;}
  showLoading();
  const headers = {'Content-Type':'application/json'};
  const key = getToken(); if(key) headers['Authorization']='Bearer '+key;
  try{
    const r = await fetch('/task', {method:'POST', headers, body:JSON.stringify(body)});
    const d = await r.json();
    if(!r.ok){out.textContent='Error '+r.status+': '+(d.detail||JSON.stringify(d)); return;}
    let txt = '=== Summary ===\n'+d.summary+'\n\n=== Steps ===\n';
    for(const s of d.steps) txt+='['+s.agent+']\n'+s.content+'\n\n---\n\n';
    out.textContent = txt;
  }catch(e){out.textContent='Fetch error: '+e;}
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
