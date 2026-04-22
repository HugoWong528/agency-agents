"""Async in-memory job queue for background task execution."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

JobStatus = str  # "pending" | "running" | "done" | "error"


@dataclass
class Job:
    id: str
    task: str
    agents: list[str]
    model: str | None
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_jobs: dict[str, Job] = {}
_queue: asyncio.Queue[str] = asyncio.Queue()


def all_jobs() -> list[Job]:
    return sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def submit_job(
    task: str,
    agents: list[str] | None = None,
    model: str | None = None,
) -> Job:
    job = Job(
        id=str(uuid.uuid4()),
        task=task,
        agents=agents or [],
        model=model,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    _jobs[job.id] = job
    _queue.put_nowait(job.id)
    logger.info("Submitted job %s: %s", job.id, task[:80])
    return job


def cancel_job(job_id: str) -> bool:
    """Cancel a pending job. Returns True on success."""
    job = _jobs.get(job_id)
    if job and job.status == "pending":
        job.status = "error"
        job.error = "Cancelled by user"
        job.finished_at = datetime.now(timezone.utc)
        return True
    return False


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _run_job(job: Job) -> None:
    import agents as agent_registry
    import context_store
    import github_tools
    from config import DEFAULT_MODEL, GITHUB_TOKEN
    from llm import chat_completion

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    logger.info("Running job %s: %s", job.id, job.task[:80])

    ctx = context_store.get()
    model = job.model or DEFAULT_MODEL
    steps: list[dict[str, Any]] = []

    # Prepend project context to the task so every agent understands the goal
    if ctx.project_context:
        full_task = (
            f"[Project Context]\n{ctx.project_context}\n\n"
            f"[Task]\n{job.task}"
        )
    else:
        full_task = job.task
    context = full_task

    try:
        if job.agents:
            selected = [a for a in (agent_registry.get_agent(s) for s in job.agents) if a]
        else:
            orchestrator = agent_registry.get_agent("agents-orchestrator")
            specialist = agent_registry.auto_select_agent(job.task)
            seen: set[str] = set()
            selected = []
            for a in filter(None, [orchestrator, specialist]):
                if a.slug not in seen:
                    seen.add(a.slug)
                    selected.append(a)

        for agent in selected:
            msgs: list[dict[str, str]] = [
                {"role": "system", "content": agent.system_prompt},
                {"role": "user", "content": context},
            ]
            result = await chat_completion(msgs, model=model)
            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage")
            steps.append({"agent": agent.slug, "content": content, "model": model, "usage": usage})
            context = (
                f"Previous agent ({agent.name}) output:\n{content}\n\n"
                f"Original task: {job.task}"
            )

        # Summarise
        summariser = agent_registry.get_agent("agents-orchestrator") or selected[-1]
        summary_msgs: list[dict[str, str]] = [
            {"role": "system", "content": summariser.system_prompt},
            {
                "role": "user",
                "content": (
                    "Summarise the following multi-agent workflow in 3-5 sentences:\n\n"
                    + "\n\n---\n\n".join(
                        f"**{s['agent']}**:\n{s['content']}" for s in steps
                    )
                ),
            },
        ]
        summary_result = await chat_completion(summary_msgs, model=model)
        summary = summary_result["choices"][0]["message"]["content"]

        # -----------------------------------------------------------------
        # Self-improvement: critic pass → specialist refine pass
        # -----------------------------------------------------------------
        if ctx.self_improve and selected:
            try:
                critic = (
                    agent_registry.get_agent("code-reviewer")
                    or agent_registry.get_agent("agents-orchestrator")
                    or selected[-1]
                )
                critique_msgs: list[dict[str, str]] = [
                    {"role": "system", "content": critic.system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Review the following output for the task: '{job.task}'\n\n"
                            f"Output:\n{summary}\n\n"
                            "List specific, actionable improvements. Be concise and direct."
                        ),
                    },
                ]
                critique_result = await chat_completion(critique_msgs, model=model)
                critique = critique_result["choices"][0]["message"]["content"]
                steps.append(
                    {"agent": "self-improve-critic", "content": critique, "model": model, "usage": None}
                )

                # Refine with the best-fit specialist
                refiner = agent_registry.auto_select_agent(job.task)
                refine_msgs: list[dict[str, str]] = [
                    {"role": "system", "content": refiner.system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Original task: {job.task}\n\n"
                            f"First draft:\n{summary}\n\n"
                            f"Reviewer feedback:\n{critique}\n\n"
                            "Provide a fully revised, improved version addressing every point of feedback."
                        ),
                    },
                ]
                refine_result = await chat_completion(refine_msgs, model=model)
                refined = refine_result["choices"][0]["message"]["content"]
                steps.append(
                    {
                        "agent": f"{refiner.slug}-refined",
                        "content": refined,
                        "model": model,
                        "usage": None,
                    }
                )
                summary = refined  # use the improved version as the final answer
                logger.info("Job %s: self-improve pass completed.", job.id)
            except Exception as improve_exc:
                logger.warning(
                    "Job %s: self-improve pass failed (continuing with original): %s",
                    job.id,
                    improve_exc,
                )

        job.status = "done"
        job.result = {"steps": steps, "summary": summary}
        logger.info("Job %s completed successfully.", job.id)

        # -----------------------------------------------------------------
        # Auto-upload result to GitHub as an issue
        # -----------------------------------------------------------------
        _MAX_ISSUE_TITLE_LEN = 72
        if ctx.auto_github_repo and GITHUB_TOKEN:
            try:
                truncated = len(job.task) > _MAX_ISSUE_TITLE_LEN
                title = (
                    f"[AI Job] {job.task[:_MAX_ISSUE_TITLE_LEN]}"
                    f"{'…' if truncated else ''}"
                )
                body = (
                    f"**Task:** {job.task}\n\n"
                    f"**Result:**\n\n{summary}\n\n"
                    f"---\n*Auto-generated by AI Auto Company · Job ID: `{job.id}`*"
                )
                await github_tools.create_issue(
                    ctx.auto_github_repo,
                    {"title": title, "body": body},
                )
                logger.info(
                    "Job %s: result uploaded to GitHub repo %s.",
                    job.id,
                    ctx.auto_github_repo,
                )
            except Exception as gh_exc:
                logger.warning(
                    "Job %s: auto-GitHub upload failed (result still saved locally): %s",
                    job.id,
                    gh_exc,
                )

    except Exception as exc:
        logger.exception("Job %s failed: %s", job.id, exc)
        job.status = "error"
        job.error = str(exc)
    finally:
        job.finished_at = datetime.now(timezone.utc)


async def worker() -> None:
    """Continuously pull jobs from the queue and execute them."""
    while True:
        job_id = await _queue.get()
        job = _jobs.get(job_id)
        if job and job.status == "pending":
            await _run_job(job)
        _queue.task_done()

