"""
Pollinations LLM client with multi-key round-robin failover.

Usage:
    from llm import chat_completion, stream_chat_completion

Both functions accept the same arguments and raise RuntimeError if all keys
are exhausted without a successful response.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from config import (
    DEFAULT_MODEL,
    KEY_COOLDOWN_SECONDS,
    MODEL_ALLOWLIST,
    POLLINATIONS_API_KEYS,
    POLLINATIONS_BASE_URL,
    REQUEST_TIMEOUT,
)
from models import Message

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal state: key rotation
# ---------------------------------------------------------------------------

# Maps key-index → unix timestamp when the key may be tried again.
_key_cooldowns: dict[int, float] = {}
_current_key_index: int = 0
_lock = asyncio.Lock()


def _available_key_indices() -> list[int]:
    """Return indices of keys that are not currently cooling down."""
    now = time.time()
    return [
        i
        for i in range(len(POLLINATIONS_API_KEYS))
        if now >= _key_cooldowns.get(i, 0)
    ]


def _mark_key_failed(index: int) -> None:
    """Put a key on cooldown after a hard failure."""
    until = time.time() + KEY_COOLDOWN_SECONDS
    _key_cooldowns[index] = until
    logger.warning("Key index %d on cooldown until %.0f", index, until)


def _validate_model(model: str | None) -> str:
    """Return *model* if allowed, else DEFAULT_MODEL."""
    if not model:
        return DEFAULT_MODEL
    if model in MODEL_ALLOWLIST:
        return model
    logger.warning(
        "Model '%s' not in allowlist; falling back to '%s'.", model, DEFAULT_MODEL
    )
    return DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Hard-error status codes that trigger key rotation
# ---------------------------------------------------------------------------

_FAILOVER_STATUSES = {401, 402, 403, 429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------


def _build_payload(
    messages: list[Message | dict[str, str]],
    model: str,
    *,
    stream: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = [
        m if isinstance(m, dict) else {"role": m.role, "content": m.content}
        for m in messages
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": normalized,
        "stream": stream,
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Non-streaming chat completion
# ---------------------------------------------------------------------------


async def chat_completion(
    messages: list[Message | dict[str, str]],
    *,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Call /v1/chat/completions and return the parsed JSON response.
    Tries all available keys with failover before raising.
    """
    model = _validate_model(model)
    url = f"{POLLINATIONS_BASE_URL}/v1/chat/completions"
    payload = _build_payload(messages, model, stream=False, extra=extra)

    if not POLLINATIONS_API_KEYS:
        # Try without auth (Pollinations allows some anonymous calls)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    last_err: Exception | None = None
    tried: set[int] = set()

    for _ in range(len(POLLINATIONS_API_KEYS) * 2):  # max attempts
        available = _available_key_indices()
        remaining = [i for i in available if i not in tried]
        if not remaining:
            break

        async with _lock:
            idx = remaining[0]

        key = POLLINATIONS_API_KEYS[idx]
        headers = {"Authorization": f"Bearer {key}"}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code in _FAILOVER_STATUSES:
                logger.warning(
                    "Key %d got HTTP %d — rotating.", idx, resp.status_code
                )
                _mark_key_failed(idx)
                tried.add(idx)
                # Build error using raise_for_status so the signature is correct
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as status_err:
                    last_err = status_err
                continue

            resp.raise_for_status()
            return resp.json()

        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning("Key %d timed out / connect error: %s", idx, exc)
            _mark_key_failed(idx)
            tried.add(idx)
            last_err = exc

    raise RuntimeError(
        f"All Pollinations API keys exhausted without success. Last error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Streaming chat completion
# ---------------------------------------------------------------------------


async def stream_chat_completion(
    messages: list[Message | dict[str, str]],
    *,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AsyncIterator[str]:
    """
    Yield SSE data lines from /v1/chat/completions (stream=True).
    Rotates keys on hard errors.
    """
    model = _validate_model(model)
    url = f"{POLLINATIONS_BASE_URL}/v1/chat/completions"
    payload = _build_payload(messages, model, stream=True, extra=extra)

    tried: set[int] = set()

    while True:
        if not POLLINATIONS_API_KEYS:
            headers = {}
            idx = -1
        else:
            available = [i for i in _available_key_indices() if i not in tried]
            if not available:
                raise RuntimeError("All Pollinations API keys exhausted (streaming).")
            idx = available[0]
            key = POLLINATIONS_API_KEYS[idx]
            headers = {"Authorization": f"Bearer {key}"}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code in _FAILOVER_STATUSES:
                        logger.warning(
                            "Stream key %d got HTTP %d — rotating.",
                            idx,
                            resp.status_code,
                        )
                        if idx >= 0:
                            _mark_key_failed(idx)
                            tried.add(idx)
                        continue
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            yield line
                        elif line:
                            yield line
            return
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning("Stream key %d error: %s", idx, exc)
            if idx >= 0:
                _mark_key_failed(idx)
                tried.add(idx)
            if not POLLINATIONS_API_KEYS:
                raise


# ---------------------------------------------------------------------------
# Convenience: generate image URL
# ---------------------------------------------------------------------------


def image_url(prompt: str, model: str = "flux", width: int = 1024, height: int = 1024) -> str:
    """Build a Pollinations image URL (no API call needed for basic flux)."""
    import urllib.parse
    encoded = urllib.parse.quote(prompt)
    return (
        f"{POLLINATIONS_BASE_URL}/image/{encoded}"
        f"?model={model}&width={width}&height={height}"
    )
