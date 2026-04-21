# AI Auto Company Setup & Deployment Guide

This guide explains how to run this repository as an **AI auto company** (multi-role, multi-agent operations) with:

- All roles available from this repository
- Pollinations API as the LLM provider
- **Free-model-only** policy
- Multiple API keys with automatic failover
- Local deployment and Railway deployment options

---

## 1) Important Reality Check

This repository is mainly:

- Agent definitions (`*.md`)
- Integration converters/installers (`scripts/convert.sh`, `scripts/install.sh`)

It is **not** a complete hosted SaaS backend by itself.  
To run a company-style autonomous system, you need an agent runtime/orchestrator (your app) that uses these agent files as system prompts.

---

## 2) What You Need From You (Owner)

Provide these to your AI team:

1. **Pollinations API keys** (recommend 2+ `sk_` keys)
2. **GitHub token** with repo access needed for your workflows
3. (Optional) **Railway account/project access** for cloud deployment
4. (Optional) Domain name if you want public access

Recommended minimum GitHub token scopes:

- Repository read/write (for code tasks)
- Pull requests/issues/workflows if agents must operate CI/CD and automation

Never commit keys/tokens into this repository.

---

## 3) Runtime Architecture (Recommended)

Build a small orchestrator service with these responsibilities:

1. Load all agent prompt files from repository categories (engineering, marketing, sales, etc.)
2. Route each user task to one or more selected roles
3. Call Pollinations `POST /v1/chat/completions`
4. Enforce model allowlist (free models only)
5. Rotate API keys automatically on errors (401/402/429/5xx)
6. Use GitHub token for repository actions when needed
7. Persist logs/history (SQLite/Postgres optional)

---

## 4) Free-Model-Only Policy

Maintain an explicit allowlist in your runtime config.  
Use only models not marked as paid in Pollinations docs (example starter list):

- `openai`
- `openai-fast`
- `qwen-coder`
- `mistral`
- `deepseek`
- `claude-fast`
- `nova-fast`

Update this allowlist periodically from `/v1/models`.

---

## 5) Multi-Key Failover Strategy (Required)

Set multiple Pollinations keys in priority order:

- `POLLINATIONS_API_KEYS=sk_key_1,sk_key_2,sk_key_3`

Failover logic:

1. Use current key
2. If response is 401/402/429/5xx or timeout, retry with next key
3. Add cooldown per failed key (for example 60-300s)
4. Continue round-robin after recovery
5. Log key index only (never log full key)

---

## 6) Local Deployment (Free)

1. Clone repository:
   - `/home/runner/work/agency-agents/agency-agents`
2. Generate integration artifacts if needed:
   - `./scripts/convert.sh`
3. Install to your preferred tool (example):
   - `./scripts/install.sh --tool copilot`
4. Start your orchestrator service locally with environment variables:
   - `POLLINATIONS_BASE_URL=https://gen.pollinations.ai`
   - `POLLINATIONS_API_KEYS=...`
   - `GITHUB_TOKEN=...`
   - `MODEL_ALLOWLIST=openai,openai-fast,qwen-coder,mistral,deepseek,claude-fast,nova-fast`
   - `POLICY_MODE=unrestricted` (if you intentionally allow broad task scope)

Use reverse proxy/auth locally if exposing on the internet.

---

## 7) Railway Deployment (Free-Tier Friendly)

1. Create a Railway project
2. Connect your GitHub fork/branch
3. Set service root to your orchestrator runtime folder
4. Add environment variables:
   - `POLLINATIONS_BASE_URL`
   - `POLLINATIONS_API_KEYS`
   - `GITHUB_TOKEN`
   - `MODEL_ALLOWLIST`
   - `POLICY_MODE`
5. Deploy and verify health endpoint
6. Add retry/failover logs and alerting

If Railway free tier is insufficient, run the same container locally or on other free/low-cost hosts.

---

## 8) “Can Do Anything” Operating Mode

If you want near-unrestricted operation:

1. Enable all repository roles for selection
2. Allow cross-domain tasks (no topic filter)
3. Permit GitHub automation actions using provided token
4. Keep hard safety controls only for credentials/security boundaries

Practical minimum guardrails (strongly recommended):

- Secret redaction in logs
- Outbound domain allowlist for tool execution
- Human approval for destructive repo actions

---

## 9) Production Checklist

- [ ] 2+ Pollinations keys configured and tested
- [ ] Free-model allowlist enforced
- [ ] API key failover tested (forced key failure)
- [ ] GitHub token scopes validated
- [ ] No secrets committed in repo
- [ ] Basic auth/rate limiting enabled for public endpoint
- [ ] Error and usage logs enabled
- [ ] Backup/rollback plan defined

---

## 10) Ongoing Operations

- Rotate keys regularly
- Re-sync agent files when this repo updates
- Rebuild integration outputs after agent changes: `./scripts/convert.sh`
- Monitor failed calls by model and key index
- Keep a fallback host (local run) if cloud quota is exhausted
