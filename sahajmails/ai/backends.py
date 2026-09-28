"""Concrete AI backends.

All of them are plain JSON POSTs over ``httpx``. Vendoring the official
``anthropic`` and ``openai`` SDKs would add tens of megabytes and a release
treadmill to gain nothing — the request we make is a single, stable endpoint.

``openai-compatible`` is the important one: Ollama, LM Studio, vLLM,
llama.cpp, Groq, OpenRouter and Together all speak the same shape, so pointing
``base_url`` at a local model keeps the "nothing leaves your machine" promise
fully intact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..errors import AIError
from .base import Completion, register_backend

__all__ = ["PROVIDERS", "AnthropicBackend", "OpenAIBackend", "OpenAICompatibleBackend"]

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 4


#: Advertised to the UI so the provider picker can explain itself.
PROVIDERS: list[dict[str, Any]] = [
    {
        "key": "anthropic",
        "label": "Anthropic (Claude)",
        "needs_key": True,
        "needs_base_url": False,
        "models": ["claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5-20251001"],
        "hint": "Create a key at console.anthropic.com. Sonnet 5 is the best value for "
        "short personalized sentences.",
    },
    {
        "key": "openai",
        "label": "OpenAI",
        "needs_key": True,
        "needs_base_url": False,
        "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"],
        "hint": "Create a key at platform.openai.com.",
    },
    {
        "key": "openai-compatible",
        "label": "Local or other (OpenAI-compatible)",
        "needs_key": False,
        "needs_base_url": True,
        "models": ["llama3.1", "qwen2.5", "mistral"],
        "hint": "Works with Ollama (http://localhost:11434/v1), LM Studio, vLLM, Groq, "
        "OpenRouter and Together. A local model needs no key and makes no cloud call.",
    },
    {
        "key": "fake",
        "label": "Offline demo (no model)",
        "needs_key": False,
        "needs_base_url": False,
        "models": ["fake-1"],
        "hint": "Generates predictable placeholder text so you can try the whole flow.",
    },
]


def _sleep_for(attempt: int, response: httpx.Response | None) -> float:
    """Honour Retry-After when the provider sends one, else exponential backoff."""
    if response is not None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), 30.0)
            except ValueError:
                pass
    return min(2.0**attempt, 16.0)


@dataclass
class _HttpBackend:
    """Shared retry, error mapping and client lifecycle."""

    api_key: str = ""
    model: str = ""
    base_url: str = ""
    name: str = "http"
    _client: httpx.Client | None = field(default=None, repr=False, init=False)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=_TIMEOUT)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        last: str = ""
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self.client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                last = f"could not reach {url}: {exc}"
                if attempt == _MAX_ATTEMPTS - 1:
                    break
                time.sleep(_sleep_for(attempt, None))
                continue

            if response.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_sleep_for(attempt, response))
                continue
            if response.status_code >= 400:
                raise AIError(
                    f"{self.name} returned {response.status_code}: {_extract_error(response)}",
                    hint=_status_hint(response.status_code),
                )
            data: dict[str, Any] = response.json()
            return data

        raise AIError(f"{self.name} did not respond: {last}", hint="Check your connection.")


def _extract_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or body)[:300]


def _status_hint(status: int) -> str:
    if status in {401, 403}:
        return "The API key was rejected. Check it, and that it has credit."
    if status == 404:
        return "That model name does not exist for this provider."
    if status == 429:
        return "Rate limited. Lower the concurrency in settings, or wait a moment."
    if status >= 500:
        return "The provider is having trouble. Try again shortly."
    return ""


@dataclass
class AnthropicBackend(_HttpBackend):
    """Anthropic Messages API, with prompt caching on the shared prefix.

    Every recipient sees the same instruction and differs only in a handful of
    variables, which is precisely the shape prompt caching exists for — it turns
    the bulk of a large run into cache reads.
    """

    name: str = "anthropic"
    model: str = "claude-sonnet-5"
    api_version: str = "2023-06-01"

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
        cacheable_prefix: str = "",
    ) -> Completion:
        if not self.api_key:
            raise AIError("No Anthropic API key set.", hint="Add one on the AI page.")

        blocks: list[dict[str, Any]] = []
        if cacheable_prefix:
            blocks.append(
                {
                    "type": "text",
                    "text": cacheable_prefix,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if system:
            blocks.append({"type": "text", "text": system})

        data = self._post(
            (self.base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages",
            {
                "x-api-key": self.api_key,
                "anthropic-version": self.api_version,
                "content-type": "application/json",
            },
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": blocks or system,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        usage = data.get("usage", {})
        return Completion(
            text="".join(parts).strip(),
            tokens_in=int(usage.get("input_tokens", 0)),
            tokens_out=int(usage.get("output_tokens", 0)),
            cached_tokens=int(usage.get("cache_read_input_tokens", 0)),
            model=str(data.get("model", self.model)),
        )


@dataclass
class OpenAIBackend(_HttpBackend):
    """OpenAI chat completions."""

    name: str = "openai"
    model: str = "gpt-4.1-mini"

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
        cacheable_prefix: str = "",
    ) -> Completion:
        if not self.api_key:
            raise AIError("No OpenAI API key set.", hint="Add one on the AI page.")
        return self._chat(
            (self.base_url or "https://api.openai.com/v1").rstrip("/"),
            {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            system=f"{cacheable_prefix}\n\n{system}".strip(),
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def _chat(
        self,
        base: str,
        headers: dict[str, str],
        *,
        system: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        data = self._post(
            f"{base}/chat/completions",
            headers,
            {
                "model": self.model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        choices = data.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = data.get("usage", {})
        return Completion(
            text=str(text).strip(),
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            model=str(data.get("model", self.model)),
        )


@dataclass
class OpenAICompatibleBackend(OpenAIBackend):
    """Any server speaking the OpenAI chat API — Ollama, LM Studio, vLLM, Groq…"""

    name: str = "openai-compatible"
    model: str = "llama3.1"

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
        cacheable_prefix: str = "",
    ) -> Completion:
        if not self.base_url:
            raise AIError(
                "No base URL set for the local model.",
                hint="For Ollama this is http://localhost:11434/v1",
            )
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return self._chat(
            self.base_url.rstrip("/"),
            headers,
            system=f"{cacheable_prefix}\n\n{system}".strip(),
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def _register_builtins() -> None:
    from .base import FakeBackend

    register_backend("anthropic", AnthropicBackend)
    register_backend("openai", OpenAIBackend)
    register_backend("openai-compatible", OpenAICompatibleBackend)
    register_backend("fake", lambda **kw: FakeBackend())


_register_builtins()
