"""LLM gateway: a LiteLLM Router over the key pool, with ordered fallback and response caching.

Each API key is its own Router deployment, named after its key id (key_1, key_2, ...).
Requests always target key_1, and the Router's `fallbacks` walk key_2, key_3, ... in order
when a key fails (429, quota, 5xx, timeouts, bad key). Cooldowns are disabled, so a failure
(real or simulated) only affects the request it happened in.

The LiteLLM in-memory cache is shared by all keys through a Router caching group, so the
cache key is effectively the model plus the full message list.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import Router
from litellm.caching.caching import Cache
from litellm.integrations.custom_logger import CustomLogger

from .simulation import status_for

logger = logging.getLogger("app.llm")

SIMULATION_METADATA_KEY = "simulated_failures"


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed for this request."""


@dataclass
class LLMResult:
    reply: str
    model: str
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int
    input_tokens: int
    output_tokens: int


class FailureSimulator(CustomLogger):
    """Makes chosen keys fail inside one request, driven by per-request metadata.

    Runs after the Router has picked a deployment and before the provider is called, so a
    simulated failure goes through exactly the same fallback path as a real one.
    """

    async def async_pre_call_deployment_hook(self, kwargs, call_type):  # type: ignore[override]
        metadata = kwargs.get("metadata") or {}
        failures = metadata.get(SIMULATION_METADATA_KEY)
        key_id = metadata.get("model_group")
        if not failures or not key_id:
            return None
        status = status_for(failures, key_id)
        if status is None:
            return None
        model = kwargs.get("model")
        message = f"simulated {status} for {key_id}"
        logger.info("simulating HTTP %s on %s", status, key_id)
        if status == 429:
            raise litellm.RateLimitError(message=message, llm_provider="simulation", model=model)
        if status == 500:
            raise litellm.InternalServerError(message=message, llm_provider="simulation", model=model)
        if status == 502:
            raise litellm.BadGatewayError(message=message, llm_provider="simulation", model=model)
        raise litellm.ServiceUnavailableError(message=message, llm_provider="simulation", model=model)


class FailureLogger(CustomLogger):
    """Logs every failed key attempt by key id (never the key itself)."""

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        metadata = (kwargs.get("litellm_params") or {}).get("metadata") or kwargs.get("metadata") or {}
        exc = kwargs.get("exception")
        logger.warning(
            "key %s failed: %s", metadata.get("model_group", "?"), type(exc).__name__ if exc else "error"
        )


_simulator = FailureSimulator()
_failure_logger = FailureLogger()


def _register_callbacks() -> None:
    for callback in (_simulator, _failure_logger):
        if callback not in litellm.callbacks:
            litellm.callbacks.append(callback)


def _preload_cache_hit_modules() -> None:
    """LiteLLM lazily imports these on the first cache hit, which takes seconds and would make
    that first hit slower than a live call. Import them at startup instead."""
    try:
        import litellm.llms.anthropic.messages.native.response_cache  # noqa: F401
        import litellm.responses.streaming_iterator  # noqa: F401
    except ImportError:
        logger.warning("could not preload LiteLLM cache-hit modules")


async def _flush_cache_writes() -> None:
    """LiteLLM writes cache entries in background tasks; wait so the next request can hit."""
    try:
        from litellm.caching.caching_handler import _PENDING_CACHE_WRITES
    except ImportError:  # internal detail of LiteLLM; harmless if it moves
        return
    pending = list(_PENDING_CACHE_WRITES)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class LLMGateway:
    def __init__(
        self,
        api_keys: list[str],
        model: str,
        timeout_s: float = 30.0,
        extra_litellm_params: dict[str, Any] | None = None,
    ):
        if not api_keys:
            raise ValueError("LLM_API_KEYS must contain at least one key")
        self.model = model
        self.key_ids = [f"key_{i}" for i in range(1, len(api_keys) + 1)]

        litellm.cache = Cache(type="local")
        litellm.suppress_debug_info = True
        _register_callbacks()
        _preload_cache_hit_modules()

        model_list = [
            {
                "model_name": key_id,
                "litellm_params": {
                    "model": model,
                    "api_key": api_key,
                    "timeout": timeout_s,
                    "max_retries": 0,
                    **(extra_litellm_params or {}),
                },
                "model_info": {"id": key_id},
            }
            for key_id, api_key in zip(self.key_ids, api_keys)
        ]
        self.router = Router(
            model_list=model_list,
            fallbacks=[{self.key_ids[0]: self.key_ids[1:]}] if len(self.key_ids) > 1 else [],
            num_retries=0,
            disable_cooldowns=True,
            caching_groups=[tuple(self.key_ids)],
        )

    @property
    def cache_enabled(self) -> bool:
        return litellm.cache is not None

    async def complete(
        self, messages: list[dict[str, str]], simulated_failures: dict[str, int] | None = None
    ) -> LLMResult:
        kwargs: dict[str, Any] = {"caching": True}
        if simulated_failures:
            # Failover demo: skip the cache read so the keys are really exercised. The result
            # is still written to the cache.
            kwargs["cache"] = {"no-cache": True}
            kwargs["metadata"] = {SIMULATION_METADATA_KEY: simulated_failures}

        try:
            response = await self.router.acompletion(model=self.key_ids[0], messages=messages, **kwargs)
        except Exception as exc:
            logger.error("all keys failed: %s", type(exc).__name__)
            raise AllProvidersUnavailable() from exc

        reply = (response.choices[0].message.content or "").strip()
        if not reply:
            logger.error("provider returned an empty reply")
            raise AllProvidersUnavailable()

        await _flush_cache_writes()

        hidden = getattr(response, "_hidden_params", {}) or {}
        cache_hit = hidden.get("cache_hit") is True
        key_id = None if cache_hit else hidden.get("model_id")
        # Fallback order is fixed (key_1 -> key_2 -> ...), so the answering key's position
        # equals the number of keys that failed before it.
        fallback_attempts = self.key_ids.index(key_id) if key_id in self.key_ids else 0
        usage = getattr(response, "usage", None)
        return LLMResult(
            reply=reply,
            model=self.model,
            cache_hit=cache_hit,
            key_id=key_id,
            fallback_attempts=fallback_attempts,
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
