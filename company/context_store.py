"""
Platform-wide context and settings store.

Settings are kept in memory and updated via the /context API endpoint.
They apply globally to every job that runs on this server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlatformContext:
    # Free-text description of your project / goals injected at the top of
    # every agent prompt so agents understand *what* they are working on.
    project_context: str = ""

    # owner/repo format (e.g. "acme/my-project").  When set and GITHUB_TOKEN
    # is configured, every completed job will automatically create a GitHub
    # issue in this repository containing the job result.
    auto_github_repo: str = ""

    # Branch to use when committing files via the auto-upload feature.
    auto_github_branch: str = "main"

    # When True the worker runs a critic+refine pass after the main agents
    # finish, improving the quality of the final answer.
    self_improve: bool = True


_ctx = PlatformContext()


def get() -> PlatformContext:
    """Return the current platform context."""
    return _ctx


def update(
    project_context: str | None = None,
    auto_github_repo: str | None = None,
    auto_github_branch: str | None = None,
    self_improve: bool | None = None,
) -> PlatformContext:
    """Apply partial updates to the platform context and return the result."""
    if project_context is not None:
        _ctx.project_context = project_context
    if auto_github_repo is not None:
        _ctx.auto_github_repo = auto_github_repo
    if auto_github_branch is not None:
        _ctx.auto_github_branch = auto_github_branch
    if self_improve is not None:
        _ctx.self_improve = self_improve
    return _ctx


def as_dict() -> dict[str, Any]:
    return {
        "project_context": _ctx.project_context,
        "auto_github_repo": _ctx.auto_github_repo,
        "auto_github_branch": _ctx.auto_github_branch,
        "self_improve": _ctx.self_improve,
    }
