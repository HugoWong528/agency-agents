"""Pydantic request/response models for the company API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / inner types
# ---------------------------------------------------------------------------


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]  # enforce valid roles
    content: str


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message / task description")
    agent: str | None = Field(
        None,
        description=(
            "Agent slug to activate (e.g. 'ai-engineer'). "
            "When omitted, an agent is auto-selected by the orchestrator."
        ),
    )
    model: str | None = Field(
        None,
        description="Pollinations model name. Defaults to server default.",
    )
    session_id: str | None = Field(
        None,
        description="Session ID for multi-turn conversation continuity.",
    )
    stream: bool = Field(False, description="Return a streaming SSE response.")


class ChatResponse(BaseModel):
    session_id: str
    agent: str
    model: str
    content: str
    usage: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Task endpoint (multi-agent workflow)
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    task: str = Field(
        ..., description="High-level task description. Orchestrator distributes it."
    )
    agents: list[str] | None = Field(
        None,
        description="Optional ordered list of agent slugs to invoke in sequence.",
    )
    model: str | None = None
    stream: bool = False


class TaskStep(BaseModel):
    agent: str
    content: str
    model: str
    usage: dict[str, Any] | None = None


class TaskResponse(BaseModel):
    steps: list[TaskStep]
    summary: str


# ---------------------------------------------------------------------------
# Agent listing
# ---------------------------------------------------------------------------


class AgentInfo(BaseModel):
    slug: str
    name: str
    description: str
    category: str
    emoji: str | None = None


class AgentListResponse(BaseModel):
    agents: list[AgentInfo]
    total: int


# ---------------------------------------------------------------------------
# GitHub integration
# ---------------------------------------------------------------------------


class GitHubRequest(BaseModel):
    action: str = Field(
        ...,
        description=(
            "GitHub action to perform. "
            "Supported: 'create_issue', 'comment_issue', 'create_pr', "
            "'list_issues', 'list_prs', 'get_file', 'update_file'."
        ),
    )
    repo: str = Field(..., description="owner/repo")
    params: dict[str, Any] = Field(default_factory=dict)


class GitHubResponse(BaseModel):
    ok: bool
    data: Any


# ---------------------------------------------------------------------------
# Image / Audio
# ---------------------------------------------------------------------------


class ImageRequest(BaseModel):
    prompt: str
    model: str | None = Field(None, description="Defaults to 'flux' (free).")
    width: int = 1024
    height: int = 1024


class AudioRequest(BaseModel):
    text: str
    voice: str = "nova"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    agents_loaded: int
    keys_configured: int
    default_model: str


# ---------------------------------------------------------------------------
# Job queue
# ---------------------------------------------------------------------------


class JobSubmitRequest(BaseModel):
    task: str = Field(..., description="Task description to run in the background.")
    agents: list[str] | None = Field(
        None,
        description="Optional ordered list of agent slugs. Auto-selected when omitted.",
    )
    model: str | None = Field(None, description="Pollinations model. Defaults to server default.")


class JobInfo(BaseModel):
    id: str
    task: str
    agents: list[str]
    model: str | None
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: str | None = None
    error: str | None = None


class JobDetailResponse(JobInfo):
    steps: list[TaskStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


class ScheduleCreateRequest(BaseModel):
    name: str = Field(..., description="Human-readable name for this schedule.")
    task: str = Field(..., description="Task description to auto-submit on schedule.")
    agents: list[str] | None = Field(None, description="Optional list of agent slugs.")
    model: str | None = None
    cron: str | None = Field(
        None,
        description="5-field cron expression: 'minute hour day month weekday'",
    )
    interval_minutes: int | None = Field(
        None,
        description="Run every N minutes. Alternative to cron.",
    )


class ScheduleInfo(BaseModel):
    id: str
    name: str
    task: str
    agents: list[str]
    model: str | None
    cron: str | None
    interval_minutes: int | None
    next_run: str | None
