# Automation & Requirements Setup (Free-First)

This guide explains:
- what the system does automatically
- what you still need to configure once
- how the system tells you something is missing
- how to provide required keys/settings

---

## 1) Will it auto-create jobs from start to finish?

Short answer: **partly**.

- ✅ **Automatic after you trigger it**: once a job is submitted, the worker runs it end-to-end automatically.
- ✅ **Automatic agent choice**: if you leave `agent`/`agents` blank, the system auto-selects.
- ✅ **Automatic recurring jobs**: if you create a schedule, jobs are auto-submitted on each run.
- ❌ **Not automatic job discovery**: it does **not** find tasks by itself unless you submit a job or create a schedule.

So you do a one-time setup + first trigger, then it can run automatically.

---

## 2) Will it request missing API keys/services automatically?

Short answer: **it reports missing setup, but you provide values manually**.

- If `POLLINATIONS_API_KEYS` is not set, startup logs warn you and requests run anonymously (less reliable/rate-limited).
- If `GITHUB_TOKEN` is not set, GitHub features are disabled and auto-GitHub upload will not run.
- The app does not open an external billing flow for you; you add keys via `.env` or deployment variables.

### Free vs paid

- **Pollinations API**: intended for free-model usage in this project.
- **GitHub token**: can be created on a free GitHub account.
- If a third-party provider later requires payment, this app cannot bypass that pricing.

---

## 3) How to provide what it needs

### A. Environment variables (required base setup)

1. Copy template:

```bash
cd company
cp .env.example .env
```

2. Edit `.env`:

```env
POLLINATIONS_API_KEYS=sk_key1,sk_key2
GITHUB_TOKEN=ghp_xxx              # optional unless you need GitHub integration
SERVER_API_KEY=your_secret_key    # optional but recommended
```

3. Start server:

```bash
python main.py
```

---

### B. Runtime context (optional automation settings)

Use **Settings** in the web UI or `POST /context` to set:

- `project_context`: injects your project goals into all jobs
- `auto_github_repo`: enables automatic GitHub issue creation for completed jobs (requires `GITHUB_TOKEN`)
- `auto_github_branch`: branch for GitHub auto-upload features
- `self_improve`: enables automatic critic+refine pass

Example:

```bash
curl -X POST http://localhost:8000/context \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_SERVER_API_KEY" \
  -d '{
    "project_context": "Build and improve my SaaS MVP",
    "auto_github_repo": "yourname/yourrepo",
    "auto_github_branch": "main",
    "self_improve": true
  }'
```

---

### C. Trigger work

- One-time background job: `POST /jobs`
- Recurring automation: `POST /schedules`

If you do not set `agents`, the system auto-selects them.

---

## 4) Practical “minimum free setup”

If you want the cheapest/free path:

1. Set `POLLINATIONS_API_KEYS`
2. (Optional) Set `SERVER_API_KEY` for security
3. Run locally (`python main.py`) or on a free tier host
4. Add `GITHUB_TOKEN` only if you need GitHub automation

That is enough to run tasks automatically after submission and to run scheduled jobs automatically.
