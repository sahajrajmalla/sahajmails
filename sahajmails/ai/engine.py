"""Filling ``{% ai %}`` slots, safely.

The rule the whole design serves: **the model fills labelled gaps and can never
touch anything else.** That is structural, not a matter of prompting — the
backend is handed one slot's instruction and that slot's variables, and its
output is capped, stripped and validated before it is allowed anywhere near a
message. Anything that fails becomes the author's own fallback text.

Three optimizations matter at real volume:

* **Cache.** Keyed on everything that could change the answer, so re-running a
  500-contact campaign after an unrelated typo costs nothing.
* **Prompt caching.** The instruction is identical for every recipient; only the
  variables differ. Backends that support it are told to cache that prefix.
* **Concurrency.** A bounded pool, because one-at-a-time over 500 contacts is
  twenty minutes of waiting.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from ..contacts import ContactList
from ..errors import AIError
from ..models import Contact
from ..template import AISlot, EmailTemplate, RenderTrace
from .base import AIBackend

__all__ = ["SlotEngine", "SlotFill", "SlotResult", "clean_output"]

_SYSTEM = (
    "You write one short fragment of an email that a human will review before "
    "it is sent. Output ONLY the fragment: no greeting, no sign-off, no "
    "quotation marks, no preamble such as 'Sure' or 'Here is', no markdown, and "
    "no explanation. Plain prose only. If you cannot follow the instruction "
    "faithfully from the facts given, reply with exactly: SKIP"
)

_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
# Models like to acknowledge before answering. These strip the acknowledgement
# without touching the answer, and are applied repeatedly because the two forms
# chain: "Sure! Here is your opener: <the actual line>".
# Punctuation after the word is required, so a company genuinely called
# "Sure Systems" keeps its name instead of being eaten by the filter.
_INTERJECTION = re.compile(
    r"^\s*(?:sure|certainly|of course|absolutely|okay|ok|got it|understood)\b\s*"
    r"[!.,:;\u2013\u2014-]+\s*",
    re.IGNORECASE,
)
_PREAMBLE = re.compile(r"^\s*(?:here(?:'s| is| are)|this is)\b[^:\n]{0,60}:\s*", re.IGNORECASE)


def clean_output(raw: str, slot: AISlot) -> str:
    """Normalise a model response and enforce the slot's constraints.

    Returns an empty string when the output is unusable, which the caller turns
    into the fallback. Being strict here is what makes the feature safe to point
    at a thousand strangers.
    """
    text = _FENCE.sub("", (raw or "").strip())
    text = _TAG.sub("", text)  # never let a model inject markup into the email
    for _ in range(3):
        stripped = _PREAMBLE.sub("", _INTERJECTION.sub("", text)).strip()
        if stripped == text:
            break
        text = stripped
    text = text.strip("\"'“”").strip()
    text = re.sub(r"\s+", " ", text)

    if not text or text.upper() == "SKIP":
        return ""

    lowered = text.casefold()
    if any(phrase.casefold() in lowered for phrase in slot.banned_phrases):
        return ""

    if slot.max_words:
        words = text.split()
        if len(words) > slot.max_words:
            # Trim at a sentence boundary if there is one, so the result reads
            # as finished rather than cut off.
            trimmed = " ".join(words[: slot.max_words])
            cut = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
            text = trimmed[: cut + 1] if cut > len(trimmed) * 0.5 else trimmed.rstrip(",;: ")
    if slot.max_chars and len(text) > slot.max_chars:
        text = text[: slot.max_chars].rsplit(" ", 1)[0].rstrip(",;: ")

    return text.strip()


@dataclass(frozen=True, slots=True)
class SlotResult:
    """What happened for one slot and one recipient."""

    email: str
    slot: str
    text: str
    source: str
    """``generated`` | ``cached`` | ``fallback`` | ``error``"""

    error: str = ""
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def ok(self) -> bool:
        return self.source in {"generated", "cached"}


@dataclass(slots=True)
class SlotFill:
    """The outcome of filling every slot for a whole contact list."""

    results: list[SlotResult] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hits: int = 0
    generated: int = 0
    fallbacks: int = 0

    def by_recipient(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for r in self.results:
            out.setdefault(r.email, {})[r.slot] = r.text
        return out

    def rows(self) -> list[tuple[str, str, str, bool, str]]:
        """``(email, slot, text, approved, generated)`` for the repository."""
        return [(r.email, r.slot, r.text, False, r.text) for r in self.results]


class SlotEngine:
    """Generates slot text for a contact list."""

    def __init__(
        self,
        backend: AIBackend,
        *,
        cache_get: Callable[[str], str | None] | None = None,
        cache_put: Callable[[str, str], None] | None = None,
        concurrency: int = 4,
        temperature: float = 0.3,
    ) -> None:
        self.backend = backend
        self.cache_get = cache_get
        self.cache_put = cache_put
        self.concurrency = max(1, min(concurrency, 16))
        self.temperature = temperature
        self._lock = threading.Lock()

    # -- prompts ----------------------------------------------------------

    def _cache_key(self, slot: AISlot, instruction: str) -> str:
        material = "␟".join(
            [
                self.backend.name,
                slot.model or self.backend.model,
                slot.name,
                instruction,
                str(slot.max_words),
                str(slot.max_chars),
                ",".join(slot.banned_phrases),
                f"{slot.temperature if slot.temperature is not None else self.temperature:.2f}",
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _shared_prefix(slot: AISlot) -> str:
        """The part of the prompt identical for every recipient."""
        parts = [_SYSTEM]
        if slot.max_words:
            parts.append(f"Hard limit: at most {slot.max_words} words.")
        if slot.max_chars:
            parts.append(f"Hard limit: at most {slot.max_chars} characters.")
        if slot.banned_phrases:
            parts.append("Never use these phrases: " + ", ".join(slot.banned_phrases) + ".")
        return "\n".join(parts)

    # -- generation --------------------------------------------------------

    def fill_one(self, slot: AISlot, instruction: str, email: str) -> SlotResult:
        """Generate one slot for one recipient, with cache and fallback."""
        key = self._cache_key(slot, instruction)

        if self.cache_get is not None:
            hit = self.cache_get(key)
            if hit is not None:
                return SlotResult(email, slot.name, hit, "cached")

        try:
            completion = self.backend.complete(
                system="",
                prompt=instruction,
                temperature=(
                    slot.temperature if slot.temperature is not None else self.temperature
                ),
                max_tokens=_token_budget(slot),
                cacheable_prefix=self._shared_prefix(slot),
            )
        except AIError as exc:
            # A flaky provider must never block a send or corrupt a message.
            return SlotResult(email, slot.name, slot.fallback, "error", error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return SlotResult(email, slot.name, slot.fallback, "error", error=str(exc))

        text = clean_output(completion.text, slot)
        if not text:
            return SlotResult(
                email,
                slot.name,
                slot.fallback,
                "fallback",
                error="the model returned nothing usable",
                tokens_in=completion.tokens_in,
                tokens_out=completion.tokens_out,
            )

        if self.cache_put is not None:
            self.cache_put(key, text)
        return SlotResult(
            email,
            slot.name,
            text,
            "generated",
            tokens_in=completion.tokens_in,
            tokens_out=completion.tokens_out,
        )

    def fill(
        self,
        *,
        template: EmailTemplate,
        contacts: ContactList | Sequence[Contact],
        on_progress: Callable[[int, int], None] | None = None,
        limit: int | None = None,
    ) -> SlotFill:
        """Generate every slot for every contact.

        The instruction is resolved per contact by rendering the template once
        in trace mode — that is how ``{{ company }}`` inside an ``ai`` block
        reaches the model with a real value.
        """
        slots = {s.name: s for s in template.slots()}
        if not slots:
            return SlotFill()

        people = list(contacts.contacts if isinstance(contacts, ContactList) else contacts)
        if limit is not None:
            people = people[:limit]

        jobs: list[tuple[AISlot, str, str]] = []
        for contact in people:
            for name, instruction in self._instructions(template, contact).items():
                slot = slots.get(name)
                if slot is not None:
                    jobs.append((slot, instruction, contact.email))

        fill = SlotFill()
        done = 0
        total = len(jobs)

        def record(result: SlotResult) -> None:
            nonlocal done
            with self._lock:
                fill.results.append(result)
                fill.tokens_in += result.tokens_in
                fill.tokens_out += result.tokens_out
                if result.source == "cached":
                    fill.cache_hits += 1
                elif result.source == "generated":
                    fill.generated += 1
                else:
                    fill.fallbacks += 1
                done += 1
                current = done
            if on_progress is not None:
                on_progress(current, total)

        if self.concurrency == 1:
            for slot, instruction, email in jobs:
                record(self.fill_one(slot, instruction, email))
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                for result in pool.map(lambda j: self.fill_one(*j), jobs):
                    record(result)

        return fill

    @staticmethod
    def _instructions(template: EmailTemplate, contact: Contact) -> dict[str, str]:
        """Render once purely to capture each slot's resolved instruction."""
        trace = RenderTrace()
        # A render that fails still populates the slots it reached, which is all
        # this pass is after.
        with contextlib.suppress(Exception):
            template.render(contact, trace=trace)
        return dict(trace.slots_seen)

    # -- estimation --------------------------------------------------------

    def estimate(self, template: EmailTemplate, contacts: ContactList) -> dict[str, Any]:
        """Predict call count and cost before anything is spent."""
        slots = template.slots()
        if not slots or not contacts:
            return {"calls": 0, "cost_usd": 0.0, "model": self.backend.model}

        sample = self._instructions(template, contacts.contacts[0])
        avg_prompt = sum(len(v) for v in sample.values()) / max(1, len(sample)) if sample else 200
        calls = len(slots) * len(contacts)
        tokens_in = int(calls * (avg_prompt / 4 + 120))
        tokens_out = int(calls * sum(_token_budget(s) for s in slots) / max(1, len(slots)) * 0.6)

        price_in, price_out = _price(self.backend.model)
        cost = (tokens_in / 1_000_000) * price_in + (tokens_out / 1_000_000) * price_out
        return {
            "calls": calls,
            "slots": len(slots),
            "contacts": len(contacts),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost, 4),
            "model": self.backend.model,
        }


def _token_budget(slot: AISlot) -> int:
    """Enough room for the requested length, and not much more."""
    if slot.max_words:
        return min(400, max(48, int(slot.max_words * 2.2)))
    if slot.max_chars:
        return min(400, max(48, int(slot.max_chars / 2)))
    return 200


# Published list prices per million tokens (input, output). Only used for the
# pre-send estimate, so being slightly stale costs nothing but a rough number.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4o-mini": (0.15, 0.6),
}


def _price(model: str) -> tuple[float, float]:
    key = model.casefold()
    for name, prices in _PRICES.items():
        if key.startswith(name):
            return prices
    return (0.0, 0.0)  # local models, and anything we do not have a price for


def slots_to_csv(results: Iterable[SlotResult]) -> str:
    """Export the review table so it can be edited in a spreadsheet."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "slot", "text", "source", "error"])
    for r in results:
        writer.writerow([r.email, r.slot, r.text, r.source, r.error])
    return buffer.getvalue()


def csv_to_slots(data: str) -> dict[str, dict[str, str]]:
    """Read an edited review file back in."""
    import csv
    import io

    out: dict[str, dict[str, str]] = {}
    for row in csv.DictReader(io.StringIO(data)):
        email = (row.get("email") or "").strip().casefold()
        slot = (row.get("slot") or "").strip()
        if email and slot:
            out.setdefault(email, {})[slot] = row.get("text") or ""
    return out


def resolve_slots(stored: Mapping[str, Mapping[str, str]], email: str) -> dict[str, str]:
    """Approved text for one recipient, if any."""
    return dict(stored.get(email.casefold(), {}))
