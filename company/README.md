# AI Auto Company — Setup & Usage Guide

> Run the full agency-agents roster as a live web service, powered by the
> [Pollinations API](https://pollinations.ai) (free models only), with
> automatic multi-key failover and built-in GitHub integration.

---

## What's Included

| File | Purpose |
|------|---------|
| `main.py` | FastAPI server — all HTTP endpoints |
| `agents.py` | Scans every `*.md` agent file and loads them into memory |
| `llm.py` | Pollinations client with round-robin key failover |
| `github_tools.py` | GitHub REST API wrapper |
| `config.py` | All configuration from environment variables |
| `models.py` | Pydantic request/response schemas |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |
| `../Dockerfile` | Container image (repo root) |
| `../railway.toml` | Railway deployment config (repo root) |

---

## Quick Start — Local (free, zero cloud)

### 1. Prerequisites

```bash
python --version   # 3.11 or 3.12
pip --version
```

### 2. Clone and install

```bash
git clone https://github.com/hugow0528/agency-agents
cd agency-agents/company
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
POLLINATIONS_API_KEYS=sk_your_key_1,sk_your_key_2
GITHUB_TOKEN=ghp_your_token
SERVER_API_KEY=any_random_secret   # optional but recommended
```

Get Pollinations keys at <https://enter.pollinations.ai>.  
Get a GitHub token at <https://github.com/settings/tokens> (scopes: `repo`, `workflow`).

### 4. Run

```bash
python main.py
```

Server starts at `http://localhost:8000`.  
Open your browser at `http://localhost:8000` for the built-in chat UI.  
API docs: `http://localhost:8000/docs`

---

## Quick Start — Docker (local or any VPS)

### Build and run

```bash
cd agency-agents          # repo root (not company/)
docker build -t ai-company .
docker run -p 8000:8000 \
  -e POLLINATIONS_API_KEYS=sk_key1,sk_key2 \
  -e GITHUB_TOKEN=ghp_xxx \
  -e SERVER_API_KEY=my_secret \
  ai-company
```

Open `http://localhost:8000`.

---

## Railway Deployment (free tier)

### Step 1 — Fork the repository

Fork `hugow0528/agency-agents` to your own GitHub account so Railway can
access it.

### Step 2 — Create a Railway project

1. Go to <https://railway.app> → **New Project** → **Deploy from GitHub Repo**
2. Select your fork
3. Railway auto-detects `Dockerfile` and `railway.toml`

### Step 3 — Set environment variables

In the Railway service settings → **Variables**, add:

| Variable | Value |
|----------|-------|
| `POLLINATIONS_API_KEYS` | `sk_key1,sk_key2,sk_key3` |
| `GITHUB_TOKEN` | `ghp_xxx` |
| `SERVER_API_KEY` | any secret string |
| `DEFAULT_MODEL` | `openai` |
| `MODEL_ALLOWLIST` | *(leave blank for default free list)* |

Railway injects `PORT` automatically — no need to set it.

### Step 4 — Deploy

Click **Deploy** (or push a commit to trigger auto-deploy).  
Railway shows logs in real time. Once healthy, you get a public URL like:

```
https://ai-company-production.up.railway.app
```

---

## API Endpoints

### `GET /health`
Returns server status, number of agents loaded, and key count.

### `GET /agents`
JSON list of every agent slug, name, description, and category.

### `GET /models`
List of allowed free models and the current default.

### `POST /chat`
Single-agent chat with optional session memory.

```json
{
  "message": "Write a Python FastAPI CRUD service",
  "agent": "backend-architect",
  "model": "openai",
  "session_id": "optional-uuid",
  "stream": false
}
```

**Agent auto-selection**: Leave `agent` blank — the server picks the best
match based on keywords in your message.

### `POST /task`
Multi-agent workflow: orchestrator + specialist.

```json
{
  "task": "Build a landing page for a SaaS startup",
  "agents": ["sprint-prioritizer", "frontend-developer"],
  "model": "openai"
}
```

Leave `agents` blank to let the orchestrator auto-select.

### `POST /github`
GitHub integration (requires `GITHUB_TOKEN`).

```json
{
  "action": "create_issue",
  "repo": "hugow0528/agency-agents",
  "params": {
    "title": "New feature request",
    "body": "Please add X",
    "labels": ["enhancement"]
  }
}
```

Supported actions: `list_issues`, `create_issue`, `comment_issue`,
`list_prs`, `create_pr`, `get_file`, `update_file`, `search_code`,
`list_workflows`, `trigger_workflow`.

### `POST /image`
Generate an image URL via Pollinations (flux model, free).

```json
{
  "prompt": "a futuristic office with AI robots",
  "width": 1024,
  "height": 1024
}
```

### `POST /audio`
Text-to-speech URL via Pollinations.

```json
{
  "text": "Hello, welcome to AI Auto Company.",
  "voice": "nova"
}
```

---

## Authentication

If `SERVER_API_KEY` is set, all requests (except `/` and `/health`) require:

```
Authorization: Bearer <your_SERVER_API_KEY>
```

The built-in web UI reads the key from `?key=<value>` in the URL:

```
http://localhost:8000/?key=my_secret
```

---

## Multi-Key Failover

Set multiple Pollinations keys:

```
POLLINATIONS_API_KEYS=sk_key1,sk_key2,sk_key3
```

The server:
1. Tries the first available key
2. On HTTP 401 / 402 / 429 / 5xx or timeout → marks that key on cooldown
3. Retries with the next key
4. Cooldown period: `KEY_COOLDOWN_SECONDS` (default 120 s)
5. Keys recover after cooldown and re-enter rotation

---

## Free Models

Only models in `MODEL_ALLOWLIST` are used.  
Default list (all free on Pollinations):

```
openai, openai-fast, qwen-coder, mistral, deepseek,
claude-fast, nova-fast, nova, glm, minimax, kimi
```

Override via env:

```
MODEL_ALLOWLIST=openai,mistral,qwen-coder
DEFAULT_MODEL=openai
```

---

## Adding More Keys or Tokens

If your AI team needs more keys or permissions, open an issue in this repo
and tag the owner.  Never commit credentials — always use environment
variables or Railway secret variables.

---

## Keeping Agents Up-to-Date

When new agents are added to the repository, they are automatically picked up
on the next server restart.  In production, redeploy or restart the service.
