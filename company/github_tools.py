"""
GitHub integration helpers.

Wraps the GitHub REST API v3 using the token from GITHUB_TOKEN.
Provides the actions exposed via the /github endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import GITHUB_TOKEN, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"


def _headers() -> dict[str, str]:
    if not GITHUB_TOKEN:
        raise ValueError(
            "GITHUB_TOKEN is not set. "
            "Provide it as an environment variable to use GitHub integration."
        )
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(f"{_BASE}{path}", headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, data: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(f"{_BASE}{path}", headers=_headers(), json=data)
        resp.raise_for_status()
        return resp.json()


async def _patch(path: str, data: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.patch(f"{_BASE}{path}", headers=_headers(), json=data)
        resp.raise_for_status()
        return resp.json()


async def _put(path: str, data: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.put(f"{_BASE}{path}", headers=_headers(), json=data)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Public actions
# ---------------------------------------------------------------------------


async def list_issues(repo: str, params: dict[str, Any]) -> Any:
    return await _get(f"/repos/{repo}/issues", params)


async def create_issue(repo: str, params: dict[str, Any]) -> Any:
    return await _post(f"/repos/{repo}/issues", params)


async def comment_issue(repo: str, params: dict[str, Any]) -> Any:
    issue_number = params.pop("issue_number")
    return await _post(f"/repos/{repo}/issues/{issue_number}/comments", params)


async def list_prs(repo: str, params: dict[str, Any]) -> Any:
    return await _get(f"/repos/{repo}/pulls", params)


async def create_pr(repo: str, params: dict[str, Any]) -> Any:
    return await _post(f"/repos/{repo}/pulls", params)


async def get_file(repo: str, params: dict[str, Any]) -> Any:
    path = params.get("path", "")
    ref = params.get("ref", "")
    query = {"ref": ref} if ref else {}
    return await _get(f"/repos/{repo}/contents/{path}", query)


async def update_file(repo: str, params: dict[str, Any]) -> Any:
    path = params.pop("path")
    return await _put(f"/repos/{repo}/contents/{path}", params)


async def search_code(repo: str, params: dict[str, Any]) -> Any:
    q = params.get("q", "")
    return await _get("/search/code", {"q": f"{q} repo:{repo}"})


async def list_workflows(repo: str, params: dict[str, Any]) -> Any:
    return await _get(f"/repos/{repo}/actions/workflows", params)


async def trigger_workflow(repo: str, params: dict[str, Any]) -> Any:
    workflow_id = params.pop("workflow_id")
    return await _post(
        f"/repos/{repo}/actions/workflows/{workflow_id}/dispatches", params
    )


async def dispatch(action: str, repo: str, params: dict[str, Any]) -> Any:
    """Route to the correct handler by action name."""
    handlers = {
        "list_issues": list_issues,
        "create_issue": create_issue,
        "comment_issue": comment_issue,
        "list_prs": list_prs,
        "create_pr": create_pr,
        "get_file": get_file,
        "update_file": update_file,
        "search_code": search_code,
        "list_workflows": list_workflows,
        "trigger_workflow": trigger_workflow,
    }
    handler = handlers.get(action)
    if not handler:
        raise ValueError(
            f"Unknown GitHub action '{action}'. "
            f"Supported: {list(handlers)}"
        )
    return await handler(repo, dict(params))
