"""AI backend contract.

Backends are deliberately tiny: given a system prompt and a user prompt, return
text. Everything that makes generation *safe* — length caps, banned phrases,
fallbacks, caching — lives in the engine, so a plugin can add a provider in
twenty lines without having to re-implement any of the guardrails.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..errors import AIError

__all__ = [
    "AIBackend",
    "Completion",
    "FakeBackend",
    "available_backends",
    "create_backend",
    "register_backend",
]


@dataclass(frozen=True, slots=True)
class Completion:
    """One model response."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
    cached_tokens: int = 0
    """Prompt tokens served from the provider's cache, when it reports them."""


@runtime_checkable
class AIBackend(Protocol):
    """Anything that can turn a prompt into text."""

    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
        cacheable_prefix: str = "",
    ) -> Completion:
        """Generate one response.

        Args:
            cacheable_prefix: The part of the system prompt that is identical
                for every recipient. Providers that support prompt caching are
                told to cache exactly this, which is where most of the cost
                saving on a large run comes from.

        Raises:
            AIError: The provider failed or returned nothing usable.
        """
        ...

    def close(self) -> None:
        """Release any connection pool."""


BackendFactory = Callable[..., AIBackend]
_REGISTRY: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register an AI backend. Plugins call this at load time."""
    _REGISTRY[name.strip().casefold()] = factory


def available_backends() -> list[str]:
    return sorted(_REGISTRY)


def create_backend(name: str, /, **kwargs: Any) -> AIBackend:
    factory = _REGISTRY.get(name.strip().casefold())
    if factory is None:
        raise AIError(
            f"Unknown AI provider {name!r}.",
            hint="Available: " + ", ".join(available_backends()),
        )
    return factory(**kwargs)


@dataclass
class FakeBackend:
    """Deterministic backend for tests, demos and offline use.

    Returns a predictable sentence built from the prompt, so a full
    generate → review → send cycle can be exercised with no network and no key.
    """

    name: str = "fake"
    model: str = "fake-1"
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_with: Exception | None = None
    reply: str | None = None

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 400,
        temperature: float = 0.4,
        cacheable_prefix: str = "",
    ) -> Completion:
        self.calls.append({"system": system, "prompt": prompt, "temperature": temperature})
        if self.fail_with is not None:
            raise self.fail_with
        if self.reply is not None:
            return Completion(text=self.reply, tokens_in=10, tokens_out=8, model=self.model)
        # Echo something shaped like a real answer so length caps get exercised.
        subject = prompt.strip().splitlines()[-1][:80] if prompt.strip() else "you"
        return Completion(
            text=f"A generated line about {subject}",
            tokens_in=len(prompt) // 4,
            tokens_out=9,
            model=self.model,
        )

    def close(self) -> None:
        return None
