"""Agent loader — scans all agent *.md files and parses their YAML frontmatter."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from config import AGENT_DIRS, REPO_ROOT

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    slug: str
    name: str
    description: str
    category: str
    system_prompt: str
    emoji: str | None = None
    vibe: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body) from an agent markdown file."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(1)
    body = text[m.end():]
    data: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data, body


def _slugify(name: str) -> str:
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Registry: slug → Agent
_registry: dict[str, Agent] = {}


def load_agents() -> dict[str, Agent]:
    """Scan all category dirs and load every agent with YAML frontmatter."""
    global _registry
    _registry = {}

    for category in AGENT_DIRS:
        dirpath = REPO_ROOT / category
        if not dirpath.is_dir():
            continue
        for md_file in sorted(dirpath.rglob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            fm, body = _parse_frontmatter(text)
            name = fm.get("name", "")
            if not name:
                continue  # not an agent file
            slug = _slugify(name)
            agent = Agent(
                slug=slug,
                name=name,
                description=fm.get("description", ""),
                category=category,
                system_prompt=body.strip(),
                emoji=fm.get("emoji") or None,
                vibe=fm.get("vibe") or None,
            )
            _registry[slug] = agent

    return _registry


def get_agent(slug: str) -> Agent | None:
    """Return agent by slug, or None if not found."""
    return _registry.get(slug)


def all_agents() -> list[Agent]:
    """Return all loaded agents sorted by slug."""
    return sorted(_registry.values(), key=lambda a: a.slug)


def find_agent_by_keyword(keyword: str) -> Agent | None:
    """
    Fuzzy find: return the best-matching agent whose slug, name, or description
    contains *keyword* (case-insensitive). Returns None if no match.
    """
    kw = keyword.lower()
    # Exact slug match first
    if kw in _registry:
        return _registry[kw]
    # Partial matches
    candidates = [
        a
        for a in _registry.values()
        if kw in a.slug or kw in a.name.lower() or kw in a.description.lower()
    ]
    return candidates[0] if candidates else None


def auto_select_agent(task: str) -> Agent:
    """
    Automatically pick the most suitable agent for a given task text using
    keyword heuristics. Falls back to the 'agents-orchestrator' then to the
    first available agent.
    """
    task_lower = task.lower()

    keyword_map: list[tuple[str, str]] = [
        # engineering
        ("frontend", "frontend-developer"),
        ("react", "frontend-developer"),
        ("vue", "frontend-developer"),
        ("css", "frontend-developer"),
        ("html", "frontend-developer"),
        ("backend", "backend-architect"),
        ("api", "backend-architect"),
        ("database", "database-optimizer"),
        ("sql", "database-optimizer"),
        ("devops", "devops-automator"),
        ("docker", "devops-automator"),
        ("kubernetes", "devops-automator"),
        ("ci/cd", "devops-automator"),
        ("security", "security-engineer"),
        ("mobile", "mobile-app-builder"),
        ("android", "mobile-app-builder"),
        ("ios", "mobile-app-builder"),
        ("machine learning", "ai-engineer"),
        ("ml ", "ai-engineer"),
        (" ai ", "ai-engineer"),
        ("llm", "ai-engineer"),
        ("prototype", "rapid-prototyper"),
        ("mvp", "rapid-prototyper"),
        ("code review", "code-reviewer"),
        ("review code", "code-reviewer"),
        ("smart contract", "solidity-smart-contract-engineer"),
        ("solidity", "solidity-smart-contract-engineer"),
        ("blockchain", "blockchain-security-auditor"),
        ("data pipeline", "data-engineer"),
        ("etl", "data-engineer"),
        ("git", "git-workflow-master"),
        ("incident", "incident-response-commander"),
        # design
        ("ui ", "ui-designer"),
        ("ux ", "ux-researcher"),
        ("design", "ui-designer"),
        ("brand", "brand-guardian"),
        ("image prompt", "image-prompt-engineer"),
        # marketing
        ("marketing", "content-creator"),
        ("seo", "seo-specialist"),
        ("social media", "social-media-strategist"),
        ("twitter", "twitter-engager"),
        ("tiktok", "tiktok-strategist"),
        ("instagram", "instagram-curator"),
        ("reddit", "reddit-community-builder"),
        ("growth", "growth-hacker"),
        ("content", "content-creator"),
        ("linkedin", "linkedin-content-creator"),
        # sales
        ("sales", "outbound-strategist"),
        ("outbound", "outbound-strategist"),
        ("proposal", "proposal-strategist"),
        ("pipeline", "pipeline-analyst"),
        ("account", "account-strategist"),
        ("discovery", "discovery-coach"),
        ("deal", "deal-strategist"),
        # product
        ("product", "product-manager"),
        ("sprint", "sprint-prioritizer"),
        ("roadmap", "product-manager"),
        ("feedback", "feedback-synthesizer"),
        # finance
        ("finance", "financial-analyst"),
        ("accounting", "bookkeeper-controller"),
        ("tax", "tax-strategist"),
        ("investment", "investment-researcher"),
        # project management
        ("project", "senior-project-manager"),
        ("jira", "jira-workflow-steward"),
        ("agile", "senior-project-manager"),
        ("scrum", "senior-project-manager"),
        # support
        ("support", "support-responder"),
        ("customer service", "customer-service"),
        ("legal", "legal-document-review"),
        ("compliance", "compliance-auditor"),
        ("hr", "hr-onboarding"),
        ("recruit", "recruitment-specialist"),
        # testing
        ("test", "test-results-analyzer"),
        ("qa", "test-results-analyzer"),
        ("performance", "performance-benchmarker"),
        ("accessibility", "accessibility-auditor"),
        ("api test", "api-tester"),
        # specialized
        ("workflow", "specialized-workflow-architect"),
        ("orchestrat", "agents-orchestrator"),
        ("strategy", "specialized-workflow-architect"),
    ]

    for keyword, target_slug in keyword_map:
        if keyword in task_lower:
            agent = get_agent(target_slug)
            if agent:
                return agent

    # Fallback chain
    for fallback in ("agents-orchestrator", "senior-developer"):
        agent = get_agent(fallback)
        if agent:
            return agent

    agents = all_agents()
    if agents:
        return agents[0]
    raise RuntimeError("No agents loaded — check REPO_ROOT and AGENT_DIRS.")
